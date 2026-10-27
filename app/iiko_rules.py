"""Confirmed Sushi House iiko identifiers and safe order-draft rules.

This module deliberately contains no write calls.  It is shared by the voice
flow and the operator review screen so that a reviewed draft can later be
translated into an iiko request without guessing identifiers.
"""

YANDEX_RESTAURANT_COURIERS_ORDER_TYPE_ID = "9e36dff5-1114-471a-8b2c-7098d2314aa4"
YANDEX_RESTAURANT_COURIERS_ORDER_TYPE_NAME = "яндекс еда курьеры ресторана"

PAYMENT_TYPE_IDS = {
    "blogger": "c76c2c8e-2ccf-4e13-8f18-e33d82d52072",
    "cash_2": "078d7403-38d6-4d50-bc14-d9a5c6e912c8",
    "cash": "09322f46-578a-d210-add7-eec222a08871",
    "card": "ff76c0fe-583b-435f-bd98-bfe68659709c",
    "starter_online": "bc31e657-87da-4b22-8de6-609fa6aaff7d",
    "bonus": "c6bda1c0-5356-4f1c-9ecb-533bc8920267",
    "yandex": "05702afb-003b-448d-8688-9bcaed771f29",
}

ROLL_99_PRODUCT_IDS = {
    "1f7beab5-7210-4c7f-9e72-855f20d9682c": "Тори Кранч за 99 рублей",
    "d66f4347-4a4d-45b0-8469-8290289bd940": "Осака за 99 рублей",
    "aac072e7-c17b-4a5b-a8fa-4888f67360c1": "Калифорния за 99 рублей",
    "2054feb7-bfea-4124-8444-53f7a2d0371e": "Долгий путь за 99 рублей",
}

ROLL_99_PRODUCT_GROUP_ID = "b4827bc1-8db4-43c5-85b6-ea30c21b2853"


def order_type_identity(order: dict) -> tuple[str, str]:
    order_type = order.get("orderType") or {}
    return (
        str(order_type.get("id") or order.get("orderTypeId") or "").casefold(),
        str(order_type.get("name") or order.get("orderTypeName") or "").strip().casefold(),
    )


def is_yandex_restaurant_couriers_order(order: dict) -> bool:
    order_type_id, order_type_name = order_type_identity(order)
    return (
        order_type_id == YANDEX_RESTAURANT_COURIERS_ORDER_TYPE_ID
        or order_type_name == YANDEX_RESTAURANT_COURIERS_ORDER_TYPE_NAME
    )


def payment_plan(method: str) -> tuple[list[str], bool]:
    """Return confirmed iiko payment IDs and whether manual review is required."""
    normalized = " ".join(str(method or "").casefold().replace("ё", "е").split())
    has_cash = "налич" in normalized
    has_card = "карт" in normalized or "терминал" in normalized
    has_bonus = "бонус" in normalized or "балл" in normalized
    if "блог" in normalized or "бартер" in normalized:
        return [PAYMENT_TYPE_IDS["blogger"]], True
    if has_cash and has_card:
        return [PAYMENT_TYPE_IDS["card"], PAYMENT_TYPE_IDS["cash"]], True
    if has_bonus and has_cash:
        return [PAYMENT_TYPE_IDS["bonus"], PAYMENT_TYPE_IDS["cash"]], True
    if has_bonus and has_card:
        return [PAYMENT_TYPE_IDS["bonus"], PAYMENT_TYPE_IDS["card"]], True
    if has_bonus:
        return [PAYMENT_TYPE_IDS["bonus"]], True
    if has_cash:
        return [PAYMENT_TYPE_IDS["cash_2"]], False
    if has_card:
        return [PAYMENT_TYPE_IDS["card"]], False
    if "онлайн" in normalized or "сайт" in normalized or "прилож" in normalized:
        return [PAYMENT_TYPE_IDS["starter_online"]], False
    return [], True
