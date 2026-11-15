from dataclasses import dataclass
import re
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from app.iiko import IikoMenuItem, IikoOrderRoute
from app.iiko_rules import PAYMENT_TYPE_IDS


PAYMENT_KINDS = {
    PAYMENT_TYPE_IDS["cash_2"]: "Cash",
    PAYMENT_TYPE_IDS["card"]: "Card",
}


class IikoOrderValidationError(ValueError):
    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(dict.fromkeys(str(issue) for issue in issues if str(issue).strip()))
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True)
class PreparedIikoOrder:
    idempotency_key: str
    route: IikoOrderRoute
    payload: dict[str, Any]


def _phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 10:
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    return f"+{digits}" if len(digits) == 11 and digits.startswith("7") else ""


def prepare_iiko_delivery_order(
    call_id: str,
    state: dict[str, Any],
    route: IikoOrderRoute,
    catalog: list[IikoMenuItem],
) -> PreparedIikoOrder:
    issues: list[str] = []
    if state.get("intent") != "new_order":
        issues.append("В iiko можно отправить только новый заказ")
    if not state.get("confirmed"):
        issues.append("Клиент не подтвердил итоговый заказ")
    if state.get("operator_required"):
        issues.append("Черновик помечен для обязательной ручной обработки")

    service_type = str(state.get("service_type") or "")
    if service_type not in {"delivery", "pickup"}:
        issues.append("Не выбран тип получения: доставка или самовывоз")

    customer_name = str(state.get("customer_name") or "").strip()
    if not customer_name:
        issues.append("Не указано имя клиента")
    phone = _phone(state.get("contact_phone") or state.get("caller_phone"))
    if not phone:
        issues.append("Телефон должен быть указан в формате +7XXXXXXXXXX")

    catalog_by_id = {item.item_id: item for item in catalog}
    order_items: list[dict[str, Any]] = []
    calculated_total = 0.0
    for raw in state.get("items") or []:
        if not isinstance(raw, dict):
            issues.append("В черновике обнаружена некорректная позиция")
            continue
        product_id = str(raw.get("item_id") or "")
        menu_item = catalog_by_id.get(product_id)
        if not menu_item:
            issues.append(f"Позиция {raw.get('name') or product_id or 'без названия'} отсутствует в актуальном меню iiko")
            continue
        try:
            amount = int(raw.get("quantity") or 0)
        except (TypeError, ValueError):
            amount = 0
        if not 1 <= amount <= 99:
            issues.append(f"Некорректное количество для позиции {menu_item.name}")
            continue
        if menu_item.price is None:
            issues.append(f"В iiko не найдена цена позиции {menu_item.name}")
            continue
        item: dict[str, Any] = {"type": "Product", "productId": product_id, "amount": amount}
        comment = str(raw.get("comment") or "").strip()
        if comment:
            item["comment"] = comment[:255]
        order_items.append(item)
        calculated_total += float(menu_item.price) * amount
    if not order_items:
        issues.append("В заказе нет подтверждённых позиций меню")

    expected_total = round(calculated_total, 2)
    try:
        draft_total = round(float(state.get("total_amount")), 2)
    except (TypeError, ValueError):
        draft_total = -1
    if not state.get("total_complete") or draft_total < 0:
        issues.append("Итоговая сумма заказа не рассчитана")
    elif abs(draft_total - expected_total) > 0.01:
        issues.append(f"Сумма черновика {draft_total:.2f} не совпадает с актуальным меню iiko {expected_total:.2f}")

    payment_ids = [str(value) for value in state.get("payment_type_ids") or [] if str(value)]
    if state.get("payment_review_required") or len(payment_ids) != 1:
        issues.append("Комбинированная, бонусная или специальная оплата требует оформления оператором в iiko")
    payment_id = payment_ids[0] if len(payment_ids) == 1 else ""
    payment_kind = PAYMENT_KINDS.get(payment_id)
    if payment_id and not payment_kind:
        issues.append("Выбранный тип оплаты пока нельзя безопасно отправить автоматически")

    if service_type == "delivery":
        if not state.get("address_confirmed"):
            issues.append("Адрес доставки не подтверждён клиентом")
        line1 = ", ".join(filter(None, (
            str(state.get("address_locality") or "").strip(),
            str(state.get("address_street_house") or "").strip(),
        )))
        if not line1:
            issues.append("Не заполнены населённый пункт, улица и дом")
    else:
        line1 = ""
        if not str(state.get("pickup_point") or "").strip():
            issues.append("Не указана точка самовывоза")

    if not route.organization_id or not route.terminal_group_id:
        issues.append("Не выбрана точка iiko")
    if issues:
        raise IikoOrderValidationError(issues)

    idempotency_key = str(uuid5(NAMESPACE_URL, f"sushi-house-inbound:{call_id}"))
    order: dict[str, Any] = {
        "id": idempotency_key,
        "phone": phone,
        "customer": {"type": "one-time", "name": customer_name},
        "orderServiceType": "DeliveryByCourier" if service_type == "delivery" else "DeliveryByClient",
        "items": order_items,
        "payments": [{
            "paymentTypeKind": payment_kind,
            "paymentTypeId": payment_id,
            "sum": expected_total,
        }],
        "comment": f"Принято голосовым помощником. Звонок {call_id}",
    }
    if service_type == "delivery":
        address: dict[str, Any] = {"type": "city", "line1": line1[:250]}
        for state_key, iiko_key in (("apartment", "flat"), ("entrance", "entrance"), ("floor", "floor"), ("doorphone", "doorphone")):
            value = str(state.get(state_key) or "").strip()
            if value:
                address[iiko_key] = value[:100]
        order["deliveryPoint"] = {"address": address}

    return PreparedIikoOrder(
        idempotency_key=idempotency_key,
        route=route,
        payload={
            "organizationId": route.organization_id,
            "terminalGroupId": route.terminal_group_id,
            "createOrderSettings": {"checkStopList": True},
            "order": order,
        },
    )
