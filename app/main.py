from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
import os

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.db import Base, SessionLocal, engine
from app.iiko import IikoClient, IikoError
from app.models import AuditEvent, IntegrationSetting, User
from app.security import decrypt_setting, encrypt_setting, get_csrf_token, hash_password, verify_csrf, verify_password
from app.settings_catalog import SETTINGS, SETTINGS_BY_KEY


templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    yield
    engine.dispose()


app = FastAPI(title="Sushi House Voice Robot", version="0.3.3", docs_url=None, redoc_url=None, lifespan=lifespan)
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
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=page_context(
            request,
            user=user,
            active="dashboard",
            generated_at=datetime.now(UTC).strftime("%d.%m.%Y %H:%M UTC"),
            configured_count=len(configured),
            services=[
                {"name": "Сервер", "state": "Работает", "tone": "ok", "meta": "FastAPI · PostgreSQL"},
                {"name": "iikoCloud", "state": "Готов к проверке" if {"iiko_api_login", "iiko_app_id", "iiko_client_secret"}.issubset(configured) else "Ожидает настройки", "tone": "ok" if {"iiko_api_login", "iiko_app_id", "iiko_client_secret"}.issubset(configured) else "wait", "meta": "Чтение без изменений"},
                {"name": "Mango SIP", "state": "Доступы сохранены" if {"mango_sip_server", "mango_sip_login", "mango_sip_password"}.issubset(configured) else "Ожидает настройки", "tone": "ok" if {"mango_sip_server", "mango_sip_login", "mango_sip_password"}.issubset(configured) else "wait", "meta": "Тестовый режим"},
            ],
        ),
    )


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
async def settings_page(request: Request, saved: str | None = None):
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
            visible_value = ""
            if record and not definition.sensitive:
                visible_value = decrypt_setting(record.encrypted_value)
            items.append({
                "definition": definition,
                "configured": bool(record),
                "value": visible_value,
                "updated_at": record.updated_at.strftime("%d.%m.%Y %H:%M") if record else None,
            })
        sections.append({"name": section_name, "items": items})
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=page_context(request, user=user, active="settings", sections=sections, saved=saved),
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
    with SessionLocal.begin() as db:
        record = db.scalar(select(IntegrationSetting).where(IntegrationSetting.key == setting_key))
        encrypted = encrypt_setting(value)
        if record:
            record.encrypted_value = encrypted
            record.updated_at = datetime.now(UTC)
        else:
            db.add(IntegrationSetting(key=setting_key, encrypted_value=encrypted))
        db.add(AuditEvent(event_type="setting_updated", actor=user.username, details=setting_key))
    return RedirectResponse(f"/settings?saved={setting_key}", status_code=303)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def readiness():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready", "database": "connected"}
