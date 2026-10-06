from contextlib import asynccontextmanager
import asyncio
from datetime import UTC, date, datetime, timedelta
import logging
import json
import os
from urllib.parse import quote_plus
import uuid

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.db import Base, SessionLocal, engine
from app.iiko import IikoClient, IikoError
from app.mango import (
    MangoApiError,
    MangoClient,
    MangoEventError,
    describe_route_result,
    parse_call_event,
    parse_route_result,
    should_route_test_call,
    verify_signature,
)
from app.knowledge_seed import seed_knowledge_base
from app.models import AuditEvent, InboundCall, IntegrationSetting, KnowledgeEntry, MangoCallEvent, MangoRouteAttempt, TestCall, User
from app.realtime import ACTIVE_CALL_STATES, RealtimeBridgeError, YandexVoiceClient, audio_bridge, pcm16_wav
from app.security import decrypt_setting, encrypt_setting, get_csrf_token, hash_password, verify_csrf, verify_password
from app.settings_catalog import SETTINGS, SETTINGS_BY_KEY
from app.sip import SIP_REQUIRED_KEYS, SipError, apply_registration, originate_test_call, registration_status
from app.yandex_voices import VOICE_ROLES, default_role, role_supported


templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)
test_call_start_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    inserted = seed_knowledge_base()
    if inserted:
        logger.info("Seeded %s knowledge base entries", inserted)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    try:
        sip_settings = load_settings(
            *SIP_REQUIRED_KEYS, "mango_sip_port", "mango_extension",
            "mango_inbound_enabled", "mango_test_phone", "mango_operator_group",
        )
        if all(sip_settings.get(key) for key in SIP_REQUIRED_KEYS):
            status = apply_registration(sip_settings)
            logger.info("Mango SIP startup status: %s", status.state)
    except SipError as exc:
        logger.warning("Mango SIP startup failed: %s", exc)
    await audio_bridge.start()
    try:
        yield
    finally:
        await audio_bridge.stop()
        engine.dispose()


app = FastAPI(title="Sushi House Voice Robot", version="0.13.0", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        "airobot.sushi03.ru",
        "5-129-249-100.sslip.io",
        "5.129.249.100",
        "127.0.0.1",
        "localhost",
        "testserver",
    ],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET"],
    max_age=8 * 60 * 60,
    same_site="strict",
    https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def user_count() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count(User.id))) or 0


def require_user(request: Request) -> User | RedirectResponse:
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/setup" if user_count() == 0 else "/login", status_code=303)
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        db.expunge(user)
        return user


def page_context(request: Request, **values):
    return {"request": request, "csrf_token": get_csrf_token(request), **values}


def load_settings(*keys: str) -> dict[str, str]:
    with SessionLocal() as db:
        records = db.scalars(select(IntegrationSetting).where(IntegrationSetting.key.in_(keys))).all()
    return {record.key: decrypt_setting(record.encrypted_value) for record in records}


def effective_voice_settings() -> dict[str, str]:
    keys = (
        "yandex_api_key",
        "yandex_folder_id",
        "yandex_gpt_model",
        "yandex_voice",
        "yandex_voice_emotion",
        "yandex_voice_speed",
        "yandex_system_prompt",
        "inbound_order_prompt",
        "mango_test_phone",
        "mango_outbound_number",
    )
    values = load_settings(*keys)
    for key in ("yandex_gpt_model", "yandex_voice", "yandex_voice_emotion", "yandex_voice_speed", "yandex_system_prompt", "inbound_order_prompt"):
        values.setdefault(key, SETTINGS_BY_KEY[key].default)
    return values


def mask_phone(phone: str) -> str:
    return f"••••{phone[-4:]}" if len(phone) >= 4 else "••••"


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request=request, name="setup.html", context=page_context(request))


@app.post("/setup")
async def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_repeat: str = Form(...),
    csrf_token: str = Form(...),
):
    verify_csrf(request, csrf_token)
    if user_count() > 0:
        raise HTTPException(status_code=409, detail="Первичная настройка уже выполнена")
    username = username.strip()
    error = None
    if len(username) < 3:
        error = "Логин должен содержать не менее трёх символов."
    elif len(password) < 12:
        error = "Пароль должен содержать не менее 12 символов."
    elif password != password_repeat:
        error = "Пароли не совпадают."
    if error:
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context=page_context(request, error=error, username=username),
            status_code=422,
        )
    with SessionLocal.begin() as db:
        user = User(username=username, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        db.add(AuditEvent(event_type="admin_created", actor=username, details="Initial administrator"))
        request.session.clear()
        request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if user_count() == 0:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context=page_context(request))


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    verify_csrf(request, csrf_token)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username.strip()))
        if not user or not verify_password(user.password_hash, password):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context=page_context(request, error="Неверный логин или пароль."),
                status_code=401,
            )
        request.session.clear()
        request.session["user_id"] = user.id
        db.add(AuditEvent(event_type="login", actor=user.username, details="Successful login"))
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request, csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with SessionLocal() as db:
        configured = set(db.scalars(select(IntegrationSetting.key)).all())
    sip_status = registration_status()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=page_context(
            request,
            user=user,
            active="dashboard",
            generated_at=datetime.now(UTC).strftime("%d.%m.%Y %H:%M UTC"),
            configured_count=len(configured),
            settings_total=len(SETTINGS),
            services=[
                {"name": "Сервер", "state": "Работает", "tone": "ok", "meta": "FastAPI · PostgreSQL"},
                {"name": "iikoCloud", "state": "Готов к проверке" if {"iiko_api_login", "iiko_app_id", "iiko_client_secret"}.issubset(configured) else "Ожидает настройки", "tone": "ok" if {"iiko_api_login", "iiko_app_id", "iiko_client_secret"}.issubset(configured) else "wait", "meta": "Чтение без изменений"},
                {"name": "Mango SIP", "state": sip_status.label, "tone": "ok" if sip_status.registered else "wait", "meta": "PJSIP · тестовый входящий контур"},
            ],
        ),
    )


@app.get("/calls", response_class=HTMLResponse)
async def calls_page(
    request: Request,
    sip_saved: str | None = None,
    sip_error: str | None = None,
    call_started: str | None = None,
    call_error: str | None = None,
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with SessionLocal() as db:
        records = db.scalars(select(MangoCallEvent).order_by(MangoCallEvent.received_at.desc()).limit(100)).all()
        route_records = db.scalars(select(MangoRouteAttempt).order_by(MangoRouteAttempt.created_at.desc()).limit(20)).all()
        call_records = db.scalars(select(TestCall).order_by(TestCall.created_at.desc()).limit(20)).all()
    events = []
    for record in records:
        events.append({
            "call_id": record.call_id,
            "state": record.call_state,
            "location": record.location or "—",
            "from_number": decrypt_setting(record.from_number_encrypted) if record.from_number_encrypted else "—",
            "to_number": decrypt_setting(record.to_number_encrypted) if record.to_number_encrypted else "—",
            "extension": record.to_extension or "—",
            "received_at": record.received_at.strftime("%d.%m.%Y %H:%M:%S"),
        })
    mango_settings = load_settings("mango_vpbx_api_key", "mango_vpbx_api_salt")
    sip_settings = load_settings(*SIP_REQUIRED_KEYS)
    sip_status = registration_status()
    voice_settings = effective_voice_settings()
    inbound_settings = load_settings("mango_inbound_enabled")
    route_attempts = [{
        "created_at": record.created_at.strftime("%d.%m.%Y %H:%M:%S"),
        "call_id": record.call_id,
        "target": record.target_extension,
        "status": record.status,
        "result": record.result_code if record.result_code is not None else "—",
        "error": record.error,
    } for record in route_records]
    missing_voice_settings = []
    if not voice_settings.get("yandex_api_key"):
        missing_voice_settings.append("Yandex Cloud API Key")
    if not voice_settings.get("yandex_folder_id"):
        missing_voice_settings.append("Yandex Folder ID")
    if not voice_settings.get("mango_test_phone"):
        missing_voice_settings.append("тестовый номер")
    test_calls = []
    status_labels = {
        "dialing": "Набор номера",
        "connected": "Разговор",
        "completed": "Завершён",
        "not_answered": "Нет ответа",
        "failed": "Ошибка",
        "interrupted": "Прерван",
    }
    for record in call_records:
        phone = decrypt_setting(record.phone_encrypted)
        test_calls.append({
            "id": record.id,
            "status": record.status,
            "status_label": status_labels.get(record.status, record.status),
            "phone": mask_phone(phone),
            "model": record.model,
            "transcript": record.transcript,
            "error": record.error,
            "created_at": record.created_at.strftime("%d.%m.%Y %H:%M:%S"),
        })
    return templates.TemplateResponse(
        request=request,
        name="calls.html",
        context=page_context(
            request,
            user=user,
            active="calls",
            events=events,
            callback_configured=len(mango_settings) == 2,
            sip_configured=all(sip_settings.get(key) for key in SIP_REQUIRED_KEYS),
            sip_status=sip_status,
            sip_saved=sip_saved,
            sip_error=sip_error,
            call_started=call_started,
            call_error=call_error,
            test_phone=mask_phone(voice_settings["mango_test_phone"]) if voice_settings.get("mango_test_phone") else "—",
            voice_model=voice_settings["yandex_gpt_model"],
            test_call_ready=sip_status.registered and not missing_voice_settings,
            test_call_blockers=missing_voice_settings + ([] if sip_status.registered else ["регистрация Mango SIP"]),
            test_calls=test_calls,
            inbound_enabled=inbound_settings.get("mango_inbound_enabled") == "on",
            route_attempts=route_attempts,
        ),
    )


@app.get("/mango/sip/status")
async def mango_sip_status(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    status = registration_status()
    return {"state": status.state, "label": status.label, "detail": status.detail, "registered": status.registered}


@app.post("/mango/sip/apply")
async def mango_sip_apply(request: Request, csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    settings = load_settings(
        *SIP_REQUIRED_KEYS, "mango_sip_port", "mango_extension",
        "mango_inbound_enabled", "mango_test_phone", "mango_operator_group",
    )
    try:
        status = apply_registration(settings)
    except SipError as exc:
        error = quote_plus(str(exc))
        return RedirectResponse(f"/calls?sip_error={error}", status_code=303)
    with SessionLocal.begin() as db:
        db.add(AuditEvent(event_type="mango_sip_applied", actor=user.username, details=status.state))
    return RedirectResponse(f"/calls?sip_saved={quote_plus(status.label)}", status_code=303)


@app.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request, saved: str | None = None):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with SessionLocal() as db:
        entries = db.scalars(
            select(KnowledgeEntry).order_by(KnowledgeEntry.sort_order, KnowledgeEntry.category, KnowledgeEntry.title)
        ).all()
    categories: dict[str, list[KnowledgeEntry]] = {}
    for entry in entries:
        categories.setdefault(entry.category, []).append(entry)
    return templates.TemplateResponse(
        request=request,
        name="knowledge.html",
        context=page_context(
            request, user=user, active="knowledge", categories=categories,
            saved=saved, review_count=sum(item.needs_review for item in entries),
        ),
    )


@app.post("/knowledge/new")
async def knowledge_create(
    request: Request,
    category: str = Form(...),
    title: str = Form(...),
    content: str = Form(...),
    next_action: str = Form(""),
    tags: str = Form(""),
    csrf_token: str = Form(...),
):
    verify_csrf(request, csrf_token)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not category.strip() or not title.strip() or not content.strip():
        raise HTTPException(status_code=422, detail="Категория, заголовок и содержание обязательны")
    with SessionLocal.begin() as db:
        entry = KnowledgeEntry(
            seed_key=f"manual-{uuid.uuid4()}", category=category.strip()[:80], title=title.strip()[:240],
            content=content.strip()[:12000], next_action=next_action.strip()[:4000], tags=tags.strip()[:1000],
            source_sheet="Создано в админке", source_rows="", sort_order=500,
        )
        db.add(entry)
        db.add(AuditEvent(event_type="knowledge_created", actor=user.username, details=title.strip()[:240]))
    return RedirectResponse("/knowledge?saved=created", status_code=303)


@app.post("/knowledge/{entry_id}")
async def knowledge_update(
    entry_id: int,
    request: Request,
    category: str = Form(...),
    title: str = Form(...),
    content: str = Form(...),
    next_action: str = Form(""),
    tags: str = Form(""),
    enabled: str | None = Form(None),
    needs_review: str | None = Form(None),
    csrf_token: str = Form(...),
):
    verify_csrf(request, csrf_token)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with SessionLocal.begin() as db:
        entry = db.get(KnowledgeEntry, entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Запись базы знаний не найдена")
        entry.category = category.strip()[:80]
        entry.title = title.strip()[:240]
        entry.content = content.strip()[:12000]
        entry.next_action = next_action.strip()[:4000]
        entry.tags = tags.strip()[:1000]
        entry.enabled = enabled == "on"
        entry.needs_review = needs_review == "on"
        entry.updated_at = datetime.now(UTC)
        db.add(AuditEvent(event_type="knowledge_updated", actor=user.username, details=str(entry_id)))
    return RedirectResponse(f"/knowledge?saved={entry_id}", status_code=303)


@app.get("/inbound", response_class=HTMLResponse)
async def inbound_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with SessionLocal() as db:
        records = db.scalars(select(InboundCall).order_by(InboundCall.created_at.desc()).limit(100)).all()
    calls = []
    for record in records:
        try:
            draft = json.loads(record.draft_order or "{}")
        except ValueError:
            draft = {}
        calls.append({
            "id": record.id,
            "created_at": record.created_at.strftime("%d.%m.%Y %H:%M:%S"),
            "status": record.status,
            "phone": mask_phone(decrypt_setting(record.phone_encrypted)) if record.phone_encrypted else "тестовый номер",
            "customer_name": record.customer_name or "—",
            "intent": record.intent,
            "service_type": {"delivery": "Доставка", "pickup": "Самовывоз"}.get(record.service_type, "—"),
            "payment": record.payment_method or "—",
            "address": record.address or "—",
            "result": record.result,
            "transcript": record.transcript,
            "draft": draft,
            "error": record.error,
        })
    inbound_settings = load_settings("mango_inbound_enabled", "mango_test_phone", "mango_transfer_delay_seconds")
    return templates.TemplateResponse(
        request=request,
        name="inbound.html",
        context=page_context(
            request, user=user, active="inbound", calls=calls,
            enabled=inbound_settings.get("mango_inbound_enabled", "off") == "on",
            test_phone=mask_phone(inbound_settings.get("mango_test_phone", "")) if inbound_settings.get("mango_test_phone") else "—",
            transfer_delay=inbound_settings.get("mango_transfer_delay_seconds", SETTINGS_BY_KEY["mango_transfer_delay_seconds"].default),
        ),
    )


@app.post("/calls/test/start")
async def start_test_call(request: Request, csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    sip_status = registration_status()
    if not sip_status.registered:
        error = quote_plus("Mango SIP ещё не зарегистрирован: тестовый звонок не запущен")
        return RedirectResponse(f"/calls?call_error={error}", status_code=303)
    settings = effective_voice_settings()
    missing = [key for key in ("yandex_api_key", "yandex_folder_id", "mango_test_phone") if not settings.get(key)]
    if missing:
        error = quote_plus("Сначала сохраните Yandex Cloud API Key, Folder ID и тестовый номер в настройках")
        return RedirectResponse(f"/calls?call_error={error}", status_code=303)
    async with test_call_start_lock:
        with SessionLocal() as db:
            active_count = db.scalar(select(func.count(TestCall.id)).where(TestCall.status.in_(ACTIVE_CALL_STATES))) or 0
        if active_count:
            error = quote_plus("Другой тестовый звонок уже выполняется")
            return RedirectResponse(f"/calls?call_error={error}", status_code=303)
        iiko_settings = load_settings("iiko_api_login", "iiko_app_id", "iiko_client_secret")
        if len(iiko_settings) != 3:
            error = quote_plus("Не заполнены настройки iiko для получения тестового заказа")
            return RedirectResponse(f"/calls?call_error={error}", status_code=303)
        try:
            async with IikoClient(
                iiko_settings["iiko_api_login"],
                iiko_settings["iiko_app_id"],
                iiko_settings["iiko_client_secret"],
            ) as iiko_client:
                test_order = await iiko_client.latest_accepted_starter_order()
        except IikoError as exc:
            error = quote_plus(f"Не удалось получить последний заказ iiko: {exc}")
            return RedirectResponse(f"/calls?call_error={error}", status_code=303)
        if not test_order:
            error = quote_plus("За последние два дня в iiko не найден подтверждённый заказ Starter")
            return RedirectResponse(f"/calls?call_error={error}", status_code=303)
        if not test_order.first_name():
            error = quote_plus("В последнем заказе Starter не указано имя клиента")
            return RedirectResponse(f"/calls?call_error={error}", status_code=303)
        call_id = str(uuid.uuid4())
        with SessionLocal.begin() as db:
            db.add(TestCall(
                id=call_id,
                status="dialing",
                phone_encrypted=encrypt_setting(settings["mango_test_phone"]),
                model=settings["yandex_gpt_model"],
            ))
        audio_bridge.prepare_call(call_id, test_order)
        try:
            originate_test_call(
                settings["mango_test_phone"],
                call_id,
                settings.get("mango_outbound_number", ""),
            )
        except SipError as exc:
            audio_bridge.clear_call(call_id)
            with SessionLocal.begin() as db:
                record = db.get(TestCall, call_id)
                record.status = "failed"
                record.error = str(exc)
                record.finished_at = datetime.now(UTC)
            error = quote_plus(str(exc))
            return RedirectResponse(f"/calls?call_error={error}", status_code=303)
        audio_bridge.watch_dialing(call_id)
    with SessionLocal.begin() as db:
        db.add(AuditEvent(event_type="test_call_started", actor=user.username, details=call_id))
    return RedirectResponse(f"/calls?call_started={quote_plus(call_id)}", status_code=303)


@app.get("/mango/events/call")
async def mango_call_endpoint_status():
    settings = load_settings("mango_vpbx_api_key", "mango_vpbx_api_salt")
    return {
        "status": "ready" if len(settings) == 2 else "awaiting_credentials",
        "endpoint": "/mango/events/call",
        "signatureVerification": len(settings) == 2,
    }


@app.post("/mango/events/call")
async def mango_call_event(request: Request):
    settings = load_settings(
        "mango_vpbx_api_key", "mango_vpbx_api_salt", "mango_inbound_enabled",
        "mango_test_phone", "mango_extension", "mango_sip_login",
    )
    if not settings.get("mango_vpbx_api_key") or not settings.get("mango_vpbx_api_salt"):
        return JSONResponse({"status": "not_configured"}, status_code=503)
    form = await request.form()
    received_key = str(form.get("vpbx_api_key") or "")
    signature = str(form.get("sign") or "")
    raw_json = str(form.get("json") or "")
    if not verify_signature(
        settings["mango_vpbx_api_key"],
        settings["mango_vpbx_api_salt"],
        received_key,
        raw_json,
        signature,
    ):
        return JSONResponse({"status": "invalid_signature"}, status_code=401)
    try:
        event = parse_call_event(raw_json)
    except MangoEventError as exc:
        return JSONResponse({"status": "invalid_event", "detail": str(exc)}, status_code=400)
    route_command_id = ""
    route_target = settings.get("mango_extension", "225").strip() or "225"
    duplicate = False
    try:
        with SessionLocal.begin() as db:
            duplicate = db.scalar(select(MangoCallEvent.id).where(
                MangoCallEvent.call_id == event.call_id,
                MangoCallEvent.sequence == event.sequence,
            )) is not None
            if not duplicate:
                db.add(MangoCallEvent(
                    entry_id=event.entry_id,
                    call_id=event.call_id,
                    sequence=event.sequence,
                    call_state=event.call_state,
                    location=event.location,
                    from_number_encrypted=encrypt_setting(event.from_number) if event.from_number else "",
                    to_number_encrypted=encrypt_setting(event.to_number) if event.to_number else "",
                    to_extension=event.to_extension,
                    line_number=event.line_number,
                    disconnect_reason=event.disconnect_reason,
                    event_timestamp=event.event_timestamp,
                ))
            if should_route_test_call(
                event,
                enabled=settings.get("mango_inbound_enabled") == "on",
                test_phone=settings.get("mango_test_phone", ""),
                target_extension=route_target,
                target_sip_login=settings.get("mango_sip_login", ""),
            ):
                attempt = db.scalar(select(MangoRouteAttempt).where(MangoRouteAttempt.call_id == event.call_id))
                if attempt is None:
                    route_command_id = f"sushi-house-inbound-{uuid.uuid4()}"
                    db.add(MangoRouteAttempt(
                        call_id=event.call_id,
                        command_id=route_command_id,
                        target_extension=route_target,
                    ))
                elif attempt.status == "failed" and attempt.attempts < 3:
                    route_command_id = f"sushi-house-inbound-{uuid.uuid4()}"
                    attempt.command_id = route_command_id
                    attempt.status = "pending"
                    attempt.result_code = None
                    attempt.error = ""
                    attempt.attempts += 1
                    attempt.updated_at = datetime.now(UTC)
    except IntegrityError:
        logger.warning("Concurrent duplicate Mango event %s/%s", event.call_id, event.sequence)
        return {"status": "duplicate"}
    if route_command_id:
        try:
            async with MangoClient(settings["mango_vpbx_api_key"], settings["mango_vpbx_api_salt"]) as client:
                await client.route_call(event.call_id, route_target, route_command_id)
            with SessionLocal.begin() as db:
                attempt = db.scalar(select(MangoRouteAttempt).where(MangoRouteAttempt.command_id == route_command_id))
                if attempt and attempt.status == "pending":
                    attempt.status = "requested"
                    attempt.updated_at = datetime.now(UTC)
            logger.info("Mango route requested for call %s to extension %s", event.call_id, route_target)
        except MangoApiError as exc:
            with SessionLocal.begin() as db:
                attempt = db.scalar(select(MangoRouteAttempt).where(MangoRouteAttempt.command_id == route_command_id))
                if attempt:
                    attempt.status = "failed"
                    attempt.error = str(exc)[:1000]
                    attempt.updated_at = datetime.now(UTC)
            logger.warning("Mango route failed for call %s: %s", event.call_id, exc)
    return {
        "status": "duplicate" if duplicate else "accepted",
        "route": "requested" if route_command_id else "not_applicable",
    }


@app.post("/mango/result/route")
async def mango_route_result(request: Request):
    settings = load_settings("mango_vpbx_api_key", "mango_vpbx_api_salt")
    if len(settings) != 2:
        return JSONResponse({"status": "not_configured"}, status_code=503)
    form = await request.form()
    received_key = str(form.get("vpbx_api_key") or "")
    signature = str(form.get("sign") or "")
    raw_json = str(form.get("json") or "")
    if not verify_signature(settings["mango_vpbx_api_key"], settings["mango_vpbx_api_salt"], received_key, raw_json, signature):
        return JSONResponse({"status": "invalid_signature"}, status_code=401)
    try:
        command_id, result = parse_route_result(raw_json)
    except MangoEventError as exc:
        return JSONResponse({"status": "invalid_result", "detail": str(exc)}, status_code=400)
    with SessionLocal.begin() as db:
        attempt = db.scalar(select(MangoRouteAttempt).where(MangoRouteAttempt.command_id == command_id))
        if attempt:
            attempt.result_code = result
            attempt.status = "succeeded" if result == 1000 else "failed"
            attempt.error = "" if result == 1000 else describe_route_result(result)
            attempt.updated_at = datetime.now(UTC)
    return {"status": "accepted"}


@app.get("/iiko", response_class=HTMLResponse)
async def iiko_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    settings = load_settings("iiko_api_login", "iiko_app_id", "iiko_client_secret")
    today = date.today()
    return templates.TemplateResponse(
        request=request,
        name="iiko.html",
        context=page_context(
            request,
            user=user,
            active="iiko",
            configured=len(settings) == 3,
            date_from=(today - timedelta(days=1)).isoformat(),
            date_to=today.isoformat(),
            result=None,
        ),
    )


@app.post("/iiko/check", response_class=HTMLResponse)
async def iiko_check(
    request: Request,
    date_from: date = Form(...),
    date_to: date = Form(...),
    csrf_token: str = Form(...),
):
    verify_csrf(request, csrf_token)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    settings = load_settings("iiko_api_login", "iiko_app_id", "iiko_client_secret")
    error = None
    result = None
    if len(settings) != 3:
        error = "Сначала сохраните API Login, App ID и Client Secret в настройках."
    elif date_to < date_from or (date_to - date_from).days > 7:
        error = "Выберите период не более 7 дней, дата окончания должна быть не раньше даты начала."
    else:
        try:
            async with IikoClient(
                settings["iiko_api_login"], settings["iiko_app_id"], settings["iiko_client_secret"]
            ) as client:
                result = await client.diagnose(date_from, date_to)
            with SessionLocal.begin() as db:
                db.add(AuditEvent(event_type="iiko_diagnostic", actor=user.username, details=f"{date_from}/{date_to}: {len(result.orders)} orders"))
        except IikoError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="iiko.html",
        context=page_context(
            request,
            user=user,
            active="iiko",
            configured=len(settings) == 3,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            result=result,
            error=error,
        ),
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: str | None = None, error: str | None = None):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with SessionLocal() as db:
        stored = {item.key: item for item in db.scalars(select(IntegrationSetting)).all()}
    sections = []
    for section_name in dict.fromkeys(item.section for item in SETTINGS):
        items = []
        for definition in (item for item in SETTINGS if item.section == section_name):
            record = stored.get(definition.key)
            visible_value = definition.default
            if record and not definition.sensitive:
                visible_value = decrypt_setting(record.encrypted_value)
            items.append({
                "definition": definition,
                "configured": bool(record),
                "value": visible_value,
                "updated_at": record.updated_at.strftime("%d.%m.%Y %H:%M") if record else None,
            })
        sections.append({"name": section_name, "items": items})
    voice_preview_ready = bool(stored.get("yandex_api_key") and stored.get("yandex_folder_id"))
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=page_context(
            request,
            user=user,
            active="settings",
            sections=sections,
            saved=saved,
            error=error,
            voice_preview_ready=voice_preview_ready,
        ),
    )


@app.post("/settings/yandex-voice-preview")
async def yandex_voice_preview(
    request: Request,
    voice: str = Form(...),
    emotion: str = Form(...),
    speed: str = Form(...),
    csrf_token: str = Form(...),
):
    verify_csrf(request, csrf_token)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if voice not in VOICE_ROLES:
        return JSONResponse({"error": "Выбран неизвестный голос Yandex"}, status_code=422)
    if not role_supported(voice, emotion):
        return JSONResponse({"error": "Выбранное амплуа не поддерживается этим голосом"}, status_code=422)
    try:
        speed_value = float(speed)
    except ValueError:
        speed_value = 0
    if not 0.1 <= speed_value <= 3.0:
        return JSONResponse({"error": "Скорость должна быть от 0.1 до 3.0"}, status_code=422)

    settings = effective_voice_settings()
    if not settings.get("yandex_api_key") or not settings.get("yandex_folder_id"):
        return JSONResponse({"error": "Сначала сохраните Yandex Cloud API Key и Folder ID"}, status_code=422)
    settings.update({
        "yandex_voice": voice,
        "yandex_voice_emotion": emotion,
        "yandex_voice_speed": f"{speed_value:.1f}",
    })
    client = YandexVoiceClient(settings)
    try:
        pcm = await client.synthesize(
            "Здравствуйте! Это Суши Хаус. Я помогу уточнить и подтвердить ваш заказ. "
            "Подскажите, пожалуйста, всё ли верно?"
        )
    except RealtimeBridgeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    finally:
        await client.close()
    with SessionLocal.begin() as db:
        db.add(AuditEvent(event_type="voice_previewed", actor=user.username, details=f"{voice}/{emotion}/{speed_value:.1f}"))
    return Response(
        content=pcm16_wav(pcm),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=yandex-voice-preview.wav"},
    )


@app.post("/settings/{setting_key}")
async def update_setting(
    setting_key: str,
    request: Request,
    value: str = Form(...),
    csrf_token: str = Form(...),
):
    verify_csrf(request, csrf_token)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    definition = SETTINGS_BY_KEY.get(setting_key)
    if not definition:
        raise HTTPException(status_code=404, detail="Неизвестная настройка")
    value = value.strip()
    if not value:
        return RedirectResponse("/settings", status_code=303)
    if len(value) > definition.max_length:
        return RedirectResponse(f"/settings?error={quote_plus('Слишком длинное значение')}", status_code=303)
    if definition.choices and value not in {choice_value for choice_value, _ in definition.choices}:
        return RedirectResponse(f"/settings?error={quote_plus('Выбрано недопустимое значение')}", status_code=303)
    if setting_key == "mango_test_phone" and (not value.startswith("+") or not value[1:].isdigit() or not 11 <= len(value[1:]) <= 15):
        error = quote_plus("Тестовый номер нужен в формате +79991234567")
        return RedirectResponse(f"/settings?error={error}", status_code=303)
    if setting_key == "yandex_folder_id" and not all(character.isalnum() or character in "-_" for character in value):
        error = quote_plus("Yandex Folder ID содержит недопустимые символы")
        return RedirectResponse(f"/settings?error={error}", status_code=303)
    if setting_key == "yandex_voice_speed":
        try:
            speed = float(value)
        except ValueError:
            speed = 0
        if not 0.1 <= speed <= 3.0:
            error = quote_plus("Скорость голоса должна быть от 0.1 до 3.0")
            return RedirectResponse(f"/settings?error={error}", status_code=303)
        value = f"{speed:.1f}"
    if setting_key == "mango_transfer_delay_seconds":
        try:
            delay = int(value)
        except ValueError:
            delay = -1
        if not 0 <= delay <= 300:
            error = quote_plus("Задержка перевода должна быть от 0 до 300 секунд")
            return RedirectResponse(f"/settings?error={error}", status_code=303)
        value = str(delay)
    if setting_key == "inbound_delivery_eta_minutes":
        try:
            eta = int(value)
        except ValueError:
            eta = 0
        if not 1 <= eta <= 300:
            error = quote_plus("Время доставки должно быть от 1 до 300 минут")
            return RedirectResponse(f"/settings?error={error}", status_code=303)
        value = str(eta)
    if setting_key == "inbound_test_bonus_balance":
        try:
            balance = float(value.replace(",", "."))
        except ValueError:
            balance = -1
        if not 0 <= balance <= 1_000_000:
            error = quote_plus("Тестовый баланс должен быть от 0 до 1000000")
            return RedirectResponse(f"/settings?error={error}", status_code=303)
        value = str(int(balance)) if balance.is_integer() else str(round(balance, 2))
    if setting_key == "yandex_voice_emotion":
        selected_voice = load_settings("yandex_voice").get("yandex_voice", SETTINGS_BY_KEY["yandex_voice"].default)
        if not role_supported(selected_voice, value):
            available = ", ".join(VOICE_ROLES.get(selected_voice, ())) or "только значение по умолчанию"
            error = quote_plus(f"Голос {selected_voice} поддерживает: {available}")
            return RedirectResponse(f"/settings?error={error}", status_code=303)
    with SessionLocal.begin() as db:
        record = db.scalar(select(IntegrationSetting).where(IntegrationSetting.key == setting_key))
        encrypted = encrypt_setting(value)
        if record:
            record.encrypted_value = encrypted
            record.updated_at = datetime.now(UTC)
        else:
            db.add(IntegrationSetting(key=setting_key, encrypted_value=encrypted))
        if setting_key == "yandex_voice":
            emotion_record = db.scalar(select(IntegrationSetting).where(IntegrationSetting.key == "yandex_voice_emotion"))
            current_emotion = decrypt_setting(emotion_record.encrypted_value) if emotion_record else SETTINGS_BY_KEY["yandex_voice_emotion"].default
            if not role_supported(value, current_emotion):
                fallback = encrypt_setting(default_role(value))
                if emotion_record:
                    emotion_record.encrypted_value = fallback
                    emotion_record.updated_at = datetime.now(UTC)
                else:
                    db.add(IntegrationSetting(key="yandex_voice_emotion", encrypted_value=fallback))
        db.add(AuditEvent(event_type="setting_updated", actor=user.username, details=setting_key))
    if setting_key in {"mango_test_phone", "mango_inbound_enabled"}:
        sip_settings = load_settings(
            *SIP_REQUIRED_KEYS, "mango_sip_port", "mango_extension",
            "mango_inbound_enabled", "mango_test_phone", "mango_operator_group",
        )
        if all(sip_settings.get(key) for key in SIP_REQUIRED_KEYS):
            try:
                apply_registration(sip_settings)
            except SipError as exc:
                error = quote_plus(f"Настройка сохранена, но SIP не применён: {exc}")
                return RedirectResponse(f"/settings?error={error}", status_code=303)
    return RedirectResponse(f"/settings?saved={setting_key}", status_code=303)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def readiness():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready", "database": "connected"}
