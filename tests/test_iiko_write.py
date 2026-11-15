import pytest

from app.iiko import IikoMenuItem, IikoOrderRoute
from app.iiko_rules import PAYMENT_TYPE_IDS
from app.iiko_write import IikoOrderValidationError, prepare_iiko_delivery_order


def valid_state() -> dict:
    return {
        "intent": "new_order",
        "confirmed": True,
        "operator_required": False,
        "customer_name": "Анна",
        "caller_phone": "+79270000000",
        "contact_phone": "+79270000000",
        "service_type": "delivery",
        "items": [{"item_id": "dish-1", "name": "Филадельфия", "quantity": 2, "comment": "2 шт"}],
        "total_complete": True,
        "total_amount": 1040,
        "payment_type_ids": [PAYMENT_TYPE_IDS["card"]],
        "payment_review_required": False,
        "address_confirmed": True,
        "address_locality": "Улан-Удэ",
        "address_street_house": "Ленина, дом 1",
        "residence_type": "apartment",
        "entrance": "2",
        "apartment": "15",
        "floor": "4",
        "doorphone": "15",
        "pickup_point": "",
    }


def test_prepares_delivery_payload_with_stable_idempotency_key():
    route = IikoOrderRoute("org-1", "terminal-1", "Смолина — доставка")
    catalog = [IikoMenuItem("dish-1", "Филадельфия", 520, "Роллы")]

    first = prepare_iiko_delivery_order("call-1", valid_state(), route, catalog)
    second = prepare_iiko_delivery_order("call-1", valid_state(), route, catalog)

    assert first.idempotency_key == second.idempotency_key
    assert first.payload["organizationId"] == "org-1"
    assert first.payload["terminalGroupId"] == "terminal-1"
    assert first.payload["createOrderSettings"] == {"checkStopList": True}
    order = first.payload["order"]
    assert order["id"] == first.idempotency_key
    assert order["orderServiceType"] == "DeliveryByCourier"
    assert order["items"] == [{"type": "Product", "productId": "dish-1", "amount": 2, "comment": "2 шт"}]
    assert order["payments"] == [{
        "paymentTypeKind": "Card",
        "paymentTypeId": PAYMENT_TYPE_IDS["card"],
        "sum": 1040.0,
    }]
    assert order["deliveryPoint"]["address"]["line1"] == "Улан-Удэ, Ленина, дом 1"
    assert order["deliveryPoint"]["address"]["flat"] == "15"


def test_prepares_pickup_without_delivery_address():
    state = valid_state()
    state.update({"service_type": "pickup", "pickup_point": "Смолина", "address_confirmed": False})
    route = IikoOrderRoute("org-1", "terminal-pickup", "Смолина — ресторан")
    prepared = prepare_iiko_delivery_order(
        "call-2", state, route, [IikoMenuItem("dish-1", "Филадельфия", 520, "Роллы")]
    )
    assert prepared.payload["order"]["orderServiceType"] == "DeliveryByClient"
    assert "deliveryPoint" not in prepared.payload["order"]


def test_blocks_unknown_item_wrong_total_and_manual_payment():
    state = valid_state()
    state["payment_review_required"] = True
    state["total_amount"] = 999
    with pytest.raises(IikoOrderValidationError) as error:
        prepare_iiko_delivery_order(
            "call-3", state, IikoOrderRoute("org-1", "terminal-1", "Точка"), []
        )
    message = str(error.value)
    assert "отсутствует в актуальном меню" in message
    assert "Комбинированная" in message
    assert "нет подтверждённых позиций" in message
