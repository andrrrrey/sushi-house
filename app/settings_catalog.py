from dataclasses import dataclass

from app.yandex_voices import ROLE_OPTIONS, VOICE_OPTIONS


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
    choices: tuple[tuple[str, str], ...] = ()
    input_type: str = "text"
    min_value: str = ""
    max_value: str = ""
    step: str = ""


SETTINGS = (
    SettingDefinition("iiko_api_login", "API Login", "iikoCloud", "Ключ доступа клиента iiko"),
    SettingDefinition("iiko_app_id", "App ID", "iikoCloud", "Идентификатор приложения", False),
    SettingDefinition("iiko_client_secret", "Client Secret", "iikoCloud", "Секрет приложения"),
    SettingDefinition("mango_sip_server", "SIP-сервер", "Mango Office", "Домен сервера Mango", False),
    SettingDefinition("mango_sip_port", "SIP-порт", "Mango Office", "Порт регистрации: 5060 или 60000", False, default="5060", placeholder="5060", max_length=5),
    SettingDefinition("mango_sip_login", "SIP-логин", "Mango Office", "Учётная запись робота", False),
    SettingDefinition("mango_sip_password", "SIP-пароль", "Mango Office", "Пароль учётной записи"),
    SettingDefinition("mango_extension", "Внутренний номер", "Mango Office", "Номер робота", False),
    SettingDefinition("mango_operator_group", "Группа операторов", "Mango Office", "Номер для перевода", False),
    SettingDefinition("mango_inbound_number", "Входящий номер", "Mango Office", "Номер приёма звонков", False),
    SettingDefinition("mango_outbound_number", "Исходящий Caller ID", "Mango Office", "Номер для исходящих", False),
    SettingDefinition("mango_vpbx_api_key", "VBPX API Key", "Mango API", "Появится после подключения API"),
    SettingDefinition("mango_vpbx_api_salt", "VBPX API Salt", "Mango API", "Ключ подписи запросов"),
    SettingDefinition(
        "yandex_api_key",
        "Yandex Cloud API Key",
        "ИИ-робот и тестовый контур",
        "Ключ сервисного аккаунта с доступом к SpeechKit и YandexGPT",
        placeholder="AQVN…",
    ),
    SettingDefinition(
        "yandex_folder_id",
        "Yandex Folder ID",
        "ИИ-робот и тестовый контур",
        "Идентификатор каталога Yandex Cloud, в котором доступны модели",
        False,
        placeholder="b1g…",
        max_length=64,
    ),
    SettingDefinition(
        "yandex_gpt_model",
        "Модель YandexGPT",
        "ИИ-робот и тестовый контур",
        "Имя модели без префикса gpt://folder-id/",
        False,
        default="yandexgpt/latest",
        placeholder="yandexgpt/latest",
        max_length=120,
    ),
    SettingDefinition(
        "yandex_voice",
        "Голос SpeechKit",
        "ИИ-робот и тестовый контур",
        "Все русскоязычные стандартные голоса SpeechKit",
        False,
        default="marina",
        placeholder="marina",
        max_length=64,
        choices=VOICE_OPTIONS,
    ),
    SettingDefinition(
        "yandex_voice_emotion",
        "Эмоция голоса",
        "ИИ-робот и тестовый контур",
        "Амплуа должно поддерживаться выбранным голосом",
        False,
        default="friendly",
        placeholder="friendly",
        max_length=64,
        choices=ROLE_OPTIONS,
    ),
    SettingDefinition(
        "yandex_voice_speed",
        "Скорость голоса",
        "ИИ-робот и тестовый контур",
        "Темп синтеза: от 0.1 до 3.0, обычная скорость — 1.0",
        False,
        default="1.0",
        placeholder="1.0",
        max_length=4,
        input_type="number",
        min_value="0.1",
        max_value="3.0",
        step="0.1",
    ),
    SettingDefinition(
        "yandex_system_prompt",
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
