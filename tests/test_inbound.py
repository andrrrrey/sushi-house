from app.iiko import IikoMenuItem
from app.inbound import apply_fast_order_step, begin_order_amendment, constrain_order_progress, is_order_amendment_request, menu_candidates, new_order_state, next_order_reply, order_dialog_step, parse_structured_response, validate_order_items


def test_menu_candidates_prefers_real_iiko_name():
    items = [
        IikoMenuItem("1", "Филадельфия", 520, "Роллы"),
        IikoMenuItem("2", "Калифорния", 430, "Роллы"),
    ]
    result = menu_candidates("Добавьте две Филадельфии", items)
    assert result and result[0].item_id == "1"


def test_structured_response_updates_local_draft():
    state = new_order_state()
    reply, updated = parse_structured_response(
        '{"reply":"Андрей, оформим доставку.","state":{"intent":"new_order","customer_name":"Андрей",'
        '"service_type":"delivery","items":[],"address":"","pickup_point":"","payment_method":"",'
        '"confirmed":false,"operator_required":false,"transfer_reason":""}}',
        state,
    )
    assert reply == "Андрей, оформим доставку."
    assert updated["customer_name"] == "Андрей"
    assert updated["service_type"] == "delivery"


def test_invalid_model_json_does_not_destroy_state():
    state = new_order_state()
    state["customer_name"] = "Анна"
    reply, updated = parse_structured_response("Уточните адрес", state)
    assert reply == "Уточните адрес"
    assert updated["customer_name"] == "Анна"


def test_unknown_model_items_are_removed_and_known_names_are_canonical():
    catalog = [IikoMenuItem("1", "Филадельфия", 520, "Роллы")]
    state = new_order_state()
    state["items"] = [
        {"item_id": "1", "name": "Филадельфия выдуманная", "quantity": 2},
        {"item_id": "hallucinated", "name": "Несуществующий ролл", "quantity": 1},
    ]
    checked = validate_order_items(state, catalog)
    assert checked["items"] == [{"item_id": "1", "name": "Филадельфия", "quantity": 2}]


def test_new_order_questions_are_strictly_sequential():
    state = new_order_state()
    assert order_dialog_step(state) == "name"
    assert next_order_reply(state) == "Как я могу к вам обращаться?"

    state["customer_name"] = "Анна"
    assert order_dialog_step(state) == "service_type"
    assert next_order_reply(state) == "Анна, оформим доставку или самовывоз?"

    state["service_type"] = "delivery"
    assert order_dialog_step(state) == "items"
    assert next_order_reply(state) == "Что хотите заказать?"

    state["items"] = [{"item_id": "1", "name": "Филадельфия", "quantity": 2}]
    assert order_dialog_step(state) == "address"
    assert "адрес доставки" in next_order_reply(state)

    state["address"] = "Ленина, 1"
    assert order_dialog_step(state) == "payment"
    assert next_order_reply(state) == "Как вам будет удобно оплатить заказ?"


def test_model_cannot_skip_future_order_steps():
    previous = new_order_state()
    proposed = dict(previous)
    proposed.update({
        "customer_name": "Анна",
        "service_type": "delivery",
        "address": "Ленина, 1",
        "payment_method": "картой",
    })
    constrained = constrain_order_progress(previous, proposed)
    assert constrained["customer_name"] == "Анна"
    assert constrained["service_type"] == ""
    assert constrained["address"] == ""
    assert constrained["payment_method"] == ""


def test_order_can_return_from_confirmation_to_item_editing():
    state = new_order_state()
    state.update({
        "customer_name": "Жаргал", "service_type": "delivery",
        "items": [{"item_id": "1", "name": "Филадельфия", "quantity": 1}],
        "address": "142 микрорайон, дом 3", "payment_method": "картой",
    })
    assert order_dialog_step(state) == "confirmation"
    assert is_order_amendment_request("Я хочу ещё дополнить заказ")
    state = begin_order_amendment(state)
    assert order_dialog_step(state) == "items_edit"
    assert next_order_reply(state) == "Конечно. Что хотите добавить, убрать или изменить?"


def test_fast_order_fields_avoid_llm_round_trip():
    state = new_order_state()
    state = apply_fast_order_step(state, "Меня зовут Жаргал")
    assert state and state["customer_name"] == "Жаргал"
    state = apply_fast_order_step(state, "Мне доставку")
    assert state and state["service_type"] == "delivery"
    state["items"] = [{"item_id": "1", "name": "Филадельфия", "quantity": 1}]
    state = apply_fast_order_step(state, "142 микрорайон дом 3 квартира 4")
    assert state and state["address"] == "142 микрорайон дом 3 квартира 4"
    state = apply_fast_order_step(state, "Картой")
    assert state and state["payment_method"] == "картой"
    state = apply_fast_order_step(state, "Да, всё верно")
    assert state and state["confirmed"] is True
