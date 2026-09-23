VOICE_OPTIONS = (
    ("filipp", "Филипп · мужской"),
    ("ermil", "Ермил · мужской"),
    ("jane", "Джейн · женский"),
    ("omazh", "Омаж · женский"),
    ("zahar", "Захар · мужской"),
    ("dasha", "Даша · женский"),
    ("julia", "Юлия · женский"),
    ("lera", "Лера · женский"),
    ("masha", "Маша · женский"),
    ("marina", "Марина · женский"),
    ("alexander", "Александр · мужской"),
    ("kirill", "Кирилл · мужской"),
    ("anton", "Антон · мужской"),
    ("madi_ru", "Мади · мужской"),
    ("saule_ru", "Сауле · женский"),
    ("zamira_ru", "Замира · женский"),
    ("zhanar_ru", "Жанар · женский"),
    ("yulduz_ru", "Юлдуз · женский"),
)

ROLE_OPTIONS = (
    ("auto", "По умолчанию для голоса"),
    ("neutral", "Нейтрально"),
    ("good", "Радостно"),
    ("evil", "Раздражённо"),
    ("strict", "Строго"),
    ("friendly", "Дружелюбно"),
    ("whisper", "Шёпотом"),
)

# Supported roles from the public SpeechKit voice catalogue. An empty tuple
# means that the voice does not expose a configurable role.
VOICE_ROLES = {
    "filipp": (),
    "ermil": ("neutral", "good"),
    "jane": ("neutral", "good", "evil"),
    "omazh": ("neutral", "evil"),
    "zahar": ("neutral", "good"),
    "dasha": ("neutral", "good", "friendly"),
    "julia": ("neutral", "strict"),
    "lera": ("neutral", "friendly"),
    "masha": ("good", "strict", "friendly"),
    "marina": ("neutral", "whisper", "friendly"),
    "alexander": ("neutral", "good"),
    "kirill": ("neutral", "strict", "good"),
    "anton": ("neutral", "good"),
    "madi_ru": (),
    "saule_ru": ("neutral", "strict", "whisper"),
    "zamira_ru": ("neutral", "strict", "friendly"),
    "zhanar_ru": ("neutral", "strict", "friendly"),
    "yulduz_ru": ("neutral", "strict", "friendly", "whisper"),
}


def role_supported(voice: str, role: str) -> bool:
    return role == "auto" or role in VOICE_ROLES.get(voice, ())


def default_role(voice: str) -> str:
    roles = VOICE_ROLES.get(voice, ())
    return roles[0] if roles else "auto"
