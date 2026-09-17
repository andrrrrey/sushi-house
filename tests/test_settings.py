from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

from app.settings_catalog import SETTINGS_BY_KEY
from app.sip import SipStatus


def test_voice_robot_settings_are_available():
    expected = {
        "openai_api_key",
        "openai_realtime_model",
        "openai_voice",
        "openai_system_prompt",
        "mango_test_phone",
    }

    assert expected.issubset(SETTINGS_BY_KEY)


def test_prompt_is_multiline_and_defaults_are_safe_for_test_mode():
    prompt = SETTINGS_BY_KEY["openai_system_prompt"]

    assert prompt.multiline is True
    assert "не меняет заказ" in prompt.default
    assert SETTINGS_BY_KEY["openai_realtime_model"].default == "gpt-realtime-2"
    assert SETTINGS_BY_KEY["openai_voice"].default == "marin"


def test_settings_template_renders_sections_from_dicts():
    environment = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    template = environment.get_template("settings.html")
    definition = SETTINGS_BY_KEY["openai_system_prompt"]

    html = template.render(
        user=SimpleNamespace(username="admin"),
        active="settings",
        csrf_token="test-token",
        saved=None,
        error=None,
        sections=[{
            "name": "ИИ-робот и тестовый контур",
            "items": [{
                "definition": definition,
                "configured": False,
                "value": definition.default,
                "updated_at": None,
            }],
        }],
    )

    assert "ИИ-робот и тестовый контур" in html
    assert "Системный промпт" in html
    assert "textarea" in html


def test_calls_template_renders_sip_registration_status():
    environment = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    template = environment.get_template("calls.html")

    html = template.render(
        user=SimpleNamespace(username="admin"),
        active="calls",
        csrf_token="test-token",
        events=[],
        callback_configured=False,
        sip_configured=True,
        sip_status=SipStatus("registered", "Зарегистрирован", "mango-registration Registered", True),
        sip_saved=None,
        sip_error=None,
        call_started=None,
        call_error=None,
        test_phone="••••4567",
        realtime_model="gpt-realtime-2",
        realtime_ready=True,
        test_call_ready=True,
        test_call_blockers=[],
        test_calls=[],
    )

    assert "Подключение Mango" in html
    assert "Зарегистрирован" in html
    assert "Применить и проверить" in html
    assert "Запустить звонок" in html
    assert "Только чтение" in html
