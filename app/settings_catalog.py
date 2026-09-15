from dataclasses import dataclass


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    section: str
    hint: str
    sensitive: bool = True
    multiline: bool = False
    default: str = ""
    placeholder: str = "Введите значение"
    max_length: int = 512


SETTINGS = (
    SettingDefinition("iiko_api_login", "API Login", "iikoCloud", "Ключ доступа клиента iiko"),
    SettingDefinition("iiko_app_id", "App ID", "iikoCloud", "Идентификатор приложения", False),
    SettingDefinition("iiko_client_secret", "Client Secret", "iikoCloud", "Секрет приложения"),
    SettingDefinition("mango_sip_server", "SIP-сервер", "Mango Office", "Домен сервера Mango", False),
    SettingDefinition("mango_sip_login", "SIP-логин", "Mango Office", "Учётная запись робота", False),
    SettingDefinition("mango_sip_password", "SIP-пароль", "Mango Office", "Пароль учётной записи"),
    SettingDefinition("mango_extension", "Внутренний номер", "Mango Office", "Номер робота", False),
    SettingDefinition("mango_operator_group", "Группа операторов", "Mango Office", "Номер для перевода", False),
    SettingDefinition("mango_inbound_number", "Входящий номер", "Mango Office", "Номер приёма звонков", False),
    SettingDefinition("mango_outbound_number", "Исходящий Caller ID", "Mango Office", "Номер для исходящих", False),
    SettingDefinition("mango_vpbx_api_key", "VBPX API Key", "Mango API", "Появится после подключения API"),
    SettingDefinition("mango_vpbx_api_salt", "VBPX API Salt", "Mango API", "Ключ подписи запросов"),
    SettingDefinition(
        "openai_api_key",
        "OpenAI API Key",
        "ИИ-робот и тестовый контур",
        "Секретный ключ проекта OpenAI для голосового агента",
        placeholder="sk-…",
    ),
    SettingDefinition(
        "openai_realtime_model",
        "Realtime-модель",
        "ИИ-робот и тестовый контур",
        "Модель OpenAI для диалога в реальном времени",
        False,
        default="gpt-realtime-2",
        placeholder="gpt-realtime-2",
    ),
    SettingDefinition(
        "openai_voice",
        "Голос",
        "ИИ-робот и тестовый контур",
        "Идентификатор голоса OpenAI, например marin или cedar",
        False,
        default="marin",
        placeholder="marin",
    ),
    SettingDefinition(
        "openai_system_prompt",
        "Системный промпт",
        "ИИ-робот и тестовый контур",
        "Правила разговора, тон и ограничения тестового робота",
        False,
        True,
        "Ты — голосовой ассистент Sushi House. Говори по-русски, кратко и вежливо. "
        "Уточни, подтверждает ли клиент тестовый заказ. Не сообщай, что заказ изменён в iiko: "
        "тестовый контур только фиксирует результат разговора и не меняет заказ. "
        "Если вопрос выходит за сценарий, предложи дождаться оператора.",
        "Опишите роль, сценарий разговора и ограничения робота",
        12000,
    ),
    SettingDefinition(
        "mango_test_phone",
        "Тестовый номер",
        "ИИ-робот и тестовый контур",
        "Единственный разрешённый номер для исходящих тестовых звонков, формат +79991234567",
        placeholder="+79991234567",
        max_length=16,
    ),
)

SETTINGS_BY_KEY = {item.key: item for item in SETTINGS}
