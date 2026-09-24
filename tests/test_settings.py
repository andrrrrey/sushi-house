from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

from app.settings_catalog import SETTINGS_BY_KEY
from app.sip import SipStatus
from app.yandex_voices import VOICE_OPTIONS, VOICE_ROLES, role_supported


def test_voice_robot_settings_are_available():
    expected = {
        "yandex_api_key",
        "yandex_folder_id",
        "yandex_gpt_model",
        "yandex_voice",
        "yandex_voice_emotion",
        "yandex_voice_speed",
        "yandex_system_prompt",
        "mango_test_phone",
    }

    assert expected.issubset(SETTINGS_BY_KEY)


def test_prompt_is_multiline_and_defaults_are_safe_for_test_mode():
    prompt = SETTINGS_BY_KEY["yandex_system_prompt"]

    assert prompt.multiline is True
    assert "не меняет заказ" in prompt.default
    assert SETTINGS_BY_KEY["yandex_gpt_model"].default == "yandexgpt/latest"
    assert SETTINGS_BY_KEY["yandex_voice"].default == "marina"
    assert len(SETTINGS_BY_KEY["yandex_voice"].choices) == 18
    assert SETTINGS_BY_KEY["yandex_voice_emotion"].default == "friendly"
    assert SETTINGS_BY_KEY["yandex_voice_speed"].default == "1.0"


def test_voice_and_emotion_render_as_selects_and_speed_as_number():
    environment = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    template = environment.get_template("settings.html")
    definitions = [
        SETTINGS_BY_KEY["yandex_voice"],
        SETTINGS_BY_KEY["yandex_voice_emotion"],
        SETTINGS_BY_KEY["yandex_voice_speed"],
    ]

    html = template.render(
        user=SimpleNamespace(username="admin"),
        active="settings",
        csrf_token="test-token",
        saved=None,
        error=None,
        voice_preview_ready=True,
        sections=[{
            "name": "ИИ-робот и тестовый контур",
            "items": [{
                "definition": definition,
                "configured": False,
                "value": definition.default,
                "updated_at": None,
            } for definition in definitions],
        }],
    )

    assert html.count("<select") == 2
    assert "Марина · женский" in html
    assert "Дружелюбно" in html
    assert 'type="number"' in html
    assert 'min="0.1"' in html
    assert 'max="3.0"' in html
    assert 'Послушать голос Yandex' in html
    assert '/settings/yandex-voice-preview' in html
    assert '<audio' in html


def test_every_offered_voice_has_an_emotion_profile():
    assert {voice for voice, _ in VOICE_OPTIONS} == set(VOICE_ROLES)
    assert role_supported("marina", "friendly") is True
    assert role_supported("filipp", "friendly") is False
    assert role_supported("filipp", "auto") is True


def test_settings_template_renders_sections_from_dicts():
    environment = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    template = environment.get_template("settings.html")
    definition = SETTINGS_BY_KEY["yandex_system_prompt"]

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
        voice_model="yandexgpt/latest",
        test_call_ready=True,
        test_call_blockers=[],
        test_calls=[],
    )

    assert "Подключение Mango" in html
    assert "Зарегистрирован" in html
    assert "Применить и проверить" in html
    assert "Запустить звонок" in html
    assert "Только чтение" in html
    assert "Yandex SpeechKit + YandexGPT" in html
