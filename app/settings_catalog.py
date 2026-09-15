from dataclasses import dataclass


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    section: str
    hint: str
    sensitive: bool = True


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
    SettingDefinition("mango_test_phone", "Тестовый номер", "Тестовый контур", "Единственный разрешённый номер для исходящих тестов"),
    SettingDefinition("openai_api_key", "OpenAI API Key", "ИИ-робот", "Используется на втором этапе"),
)

SETTINGS_BY_KEY = {item.key: item for item in SETTINGS}
