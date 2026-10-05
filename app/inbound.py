from difflib import SequenceMatcher
import json
import re
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.iiko import IikoMenuItem
from app.models import InboundCall, KnowledgeEntry


INTENT_LABELS = {
    "new_order": "новый заказ",
    "complaint": "жалоба",
    "change_order": "изменение принятого заказа",
    "refund": "возврат оплаты",
    "faq": "вопрос",
    "operator": "оператор",
}


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[а-яa-z0-9]+", value.casefold().replace("ё", "е")))


def menu_candidates(query: str, items: list[IikoMenuItem], limit: int = 30) -> list[IikoMenuItem]:
    normalized_query = normalize_text(query)
    query_words = {word for word in normalized_query.split() if len(word) > 2}
    ranked: list[tuple[float, IikoMenuItem]] = []
    for item in items:
        name = normalize_text(item.name)
        name_words = set(name.split())
        overlap = len(query_words & name_words)
        substring = 1.0 if name and name in normalized_query else 0.0
        fuzzy = SequenceMatcher(None, normalized_query, name).ratio() if normalized_query else 0.0
        word_fuzzy = max(
            (SequenceMatcher(None, query_word, name_word).ratio() for query_word in query_words for name_word in name_words),
            default=0.0,
        )
        score = substring * 4 + overlap * 1.5 + fuzzy + word_fuzzy
        if score >= 1.2:
            ranked.append((score, item))
    ranked.sort(key=lambda row: (-row[0], row[1].name))
    return [item for _, item in ranked[:limit]]


def knowledge_context(limit: int = 30) -> str:
    with SessionLocal() as db:
        entries = db.scalars(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.enabled.is_(True), KnowledgeEntry.needs_review.is_(False))
            .order_by(KnowledgeEntry.sort_order)
            .limit(limit)
        ).all()
    chunks = []
    for entry in entries:
        value = f"[{entry.category}] {entry.title}: {entry.content}"
        if entry.next_action:
            value += f" Действие: {entry.next_action}"
        chunks.append(value)
    return "\n".join(chunks)


def new_order_state() -> dict[str, Any]:
    return {
        "intent": "new_order",
        "customer_name": "",
        "service_type": "",
        "items": [],
        "address": "",
        "pickup_point": "",
        "payment_method": "",
        "confirmed": False,
        "editing_order": False,
        "operator_required": False,
        "transfer_reason": "",
    }


ORDER_FIELDS = (
    "customer_name", "service_type", "items", "address", "pickup_point", "payment_method", "confirmed", "editing_order"
)


AMENDMENT_STEMS = (
    "добав", "дополн", "забыл", "забыли", "включ", "исправ", "измен", "убер", "удал", "замен", "неверн", "неправил",
)


def is_order_amendment_request(text: str) -> bool:
    normalized = normalize_text(text)
    words = set(normalized.split())
    return (
        "нет" in words
        or "ошибка" in words
        or any(stem in normalized for stem in AMENDMENT_STEMS)
    )


def begin_order_amendment(state: dict[str, Any]) -> dict[str, Any]:
    updated = dict(state)
    updated["confirmed"] = False
    updated["editing_order"] = True
    return updated


def order_dialog_step(state: dict[str, Any]) -> str:
    if str(state.get("intent") or "new_order") != "new_order" or state.get("operator_required"):
        return "other"
    if not str(state.get("customer_name") or "").strip():
        return "name"
    if state.get("service_type") not in {"delivery", "pickup"}:
        return "service_type"
    if not state.get("items"):
        return "items"
    if state.get("editing_order"):
        return "items_edit"
    if state["service_type"] == "delivery" and not str(state.get("address") or "").strip():
        return "address"
    if state["service_type"] == "pickup" and not str(state.get("pickup_point") or "").strip():
        return "pickup_point"
    if not str(state.get("payment_method") or "").strip():
        return "payment"
    if not state.get("confirmed"):
        return "confirmation"
    return "complete"


def constrain_order_progress(previous: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """Accept only the field requested on this turn; later details are collected separately."""
    if previous.get("intent", "new_order") != "new_order" or proposed.get("intent", "new_order") != "new_order":
        return proposed
    step = order_dialog_step(previous)
    allowed = {
        "name": {"customer_name"},
        "service_type": {"service_type"},
        "items": {"items"},
        "items_edit": {"items", "editing_order"},
        "address": {"address"},
        "pickup_point": {"pickup_point"},
        "payment": {"payment_method"},
        "confirmation": set(ORDER_FIELDS),
        "complete": set(ORDER_FIELDS),
    }.get(step, set())
    result = dict(proposed)
    for key in ORDER_FIELDS:
        if key not in allowed:
            result[key] = previous.get(key)
    if step not in {"confirmation", "complete", "items_edit"}:
        result["confirmed"] = False
    return result


def next_order_reply(state: dict[str, Any]) -> str | None:
    step = order_dialog_step(state)
    name = str(state.get("customer_name") or "").strip()
    prefix = f"{name}, " if name else ""
    if step == "other":
        return None
    if step == "name":
        return "Как я могу к вам обращаться?"
    if step == "service_type":
        return f"{prefix}оформим доставку или самовывоз?"
    if step == "items":
        return "Что хотите заказать?"
    if step == "items_edit":
        return "Конечно. Что хотите добавить, убрать или изменить?"
    if step == "address":
        return "Назовите, пожалуйста, адрес доставки."
    if step == "pickup_point":
        return "Из какой точки Суши Хаус вам будет удобно забрать заказ?"
    if step == "payment":
        return "Как вам будет удобно оплатить заказ?"
    if step == "confirmation":
        items = ", ".join(
            f"{int(item.get('quantity') or 1)} — {item.get('name')}"
            for item in state.get("items") or []
            if isinstance(item, dict) and item.get("name")
        )
        fulfillment = (
            f"доставка по адресу {state.get('address')}"
            if state.get("service_type") == "delivery"
            else f"самовывоз из точки {state.get('pickup_point')}"
        )
        return f"{prefix}повторяю заказ: {items}. {fulfillment}. Оплата: {state.get('payment_method')}. Всё верно?"
    return f"{prefix}спасибо, я зафиксировала заказ в тестовом журнале."


def apply_fast_order_step(state: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Handle unambiguous order fields locally to avoid an LLM round trip."""
    step = order_dialog_step(state)
    normalized = normalize_text(text)
    updated = dict(state)
    if step == "name":
        value = re.sub(r"^(?:меня\s+зовут|мое\s+имя|это)\s+", "", text.strip(), flags=re.IGNORECASE).strip(" .,!?")
        rejected = ("заказ", "достав", "самовывоз", "жалоб", "оператор", "оплат")
        if not value or len(value.split()) > 4 or any(stem in normalize_text(value) for stem in rejected):
            return None
        updated["customer_name"] = value.title()
        return updated
    if step == "service_type":
        if "самовывоз" in normalized or "заберу" in normalized or "забрать" in normalized:
            updated["service_type"] = "pickup"
            return updated
        if "достав" in normalized or "привез" in normalized:
            updated["service_type"] = "delivery"
            return updated
        return None
    if step == "address" and len(normalized) >= 3:
        updated["address"] = text.strip(" .")
        return updated
    if step == "pickup_point" and len(normalized) >= 3:
        updated["pickup_point"] = text.strip(" .")
        return updated
    if step == "payment":
        if "карт" in normalized or "терминал" in normalized:
            updated["payment_method"] = "картой"
            return updated
        if "налич" in normalized:
            updated["payment_method"] = "наличными"
            return updated
        if "онлайн" in normalized or "ссылк" in normalized:
            updated["payment_method"] = "онлайн"
            return updated
        return None
    if step == "confirmation" and not is_order_amendment_request(text):
        words = set(normalized.split())
        if words & {"да", "верно", "правильно", "подтверждаю"}:
            updated["confirmed"] = True
            return updated
    return None


def parse_structured_response(raw: str, previous: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except (ValueError, TypeError):
        return raw.strip(), previous
    if not isinstance(payload, dict):
        return raw.strip(), previous
    reply = str(payload.get("reply") or "").strip()
    state = dict(previous)
    incoming = payload.get("state")
    if isinstance(incoming, dict):
        for key in state:
            if key in incoming and isinstance(incoming[key], type(state[key])):
                state[key] = incoming[key]
    if state["intent"] not in INTENT_LABELS:
        state["intent"] = "operator"
        state["operator_required"] = True
    if state["service_type"] not in {"", "delivery", "pickup"}:
        state["service_type"] = ""
    return reply, state


def validate_order_items(state: dict[str, Any], catalog: list[IikoMenuItem]) -> dict[str, Any]:
    """Canonicalize model output against the live iiko menu; unknown items never survive."""
    by_id = {item.item_id: item for item in catalog}
    cleaned = []
    for raw in state.get("items") or []:
        if not isinstance(raw, dict):
            continue
        item = by_id.get(str(raw.get("item_id") or ""))
        if not item:
            continue
        try:
            quantity = int(raw.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1
        if not 1 <= quantity <= 99:
            continue
        cleaned.append({"item_id": item.item_id, "name": item.name, "quantity": quantity})
    state = dict(state)
    state["items"] = cleaned
    return state


def save_inbound_state(call_id: str, state: dict[str, Any], transcript_line: str = "") -> None:
    with SessionLocal.begin() as db:
        record = db.get(InboundCall, call_id)
        if not record:
            return
        record.customer_name = str(state.get("customer_name") or "")[:160]
        record.intent = str(state.get("intent") or "new_order")[:40]
        record.service_type = str(state.get("service_type") or "")[:24]
        record.address = str(state.get("address") or state.get("pickup_point") or "")[:4000]
        record.payment_method = str(state.get("payment_method") or "")[:80]
        record.result = "operator_required" if state.get("operator_required") else ("confirmed_draft" if state.get("confirmed") else "draft")
        record.transfer_reason = str(state.get("transfer_reason") or "")[:4000]
        record.draft_order = json.dumps(state, ensure_ascii=False)
        if transcript_line:
            record.transcript = f"{record.transcript}\n{transcript_line}".strip()[-30000:]
