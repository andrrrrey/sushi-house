import asyncio
from datetime import date, timedelta
import re
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.iiko import IikoClient
from app.models import AuditEvent, IntegrationSetting
from app.security import encrypt_setting


IIKO_LABELS = {
    "API Login": "iiko_api_login",
    "App ID": "iiko_app_id",
    "Client Secret": "iiko_client_secret",
}


def import_iiko(markdown: str) -> None:
    values = {}
    for label, key in IIKO_LABELS.items():
        match = re.search(rf"^- {re.escape(label)}:\s*`([^`]+)`", markdown, re.MULTILINE)
        if not match:
            raise SystemExit(f"Не найдено обязательное поле: {label}")
        values[key] = match.group(1)
    with SessionLocal.begin() as db:
        for key, value in values.items():
            record = db.scalar(select(IntegrationSetting).where(IntegrationSetting.key == key))
            if record:
                record.encrypted_value = encrypt_setting(value)
            else:
                db.add(IntegrationSetting(key=key, encrypted_value=encrypt_setting(value)))
        db.add(AuditEvent(event_type="iiko_settings_imported", actor="system", details="3 settings imported over SSH"))
    print("iiko: сохранено 3 зашифрованных параметра")


async def diagnose() -> None:
    with SessionLocal() as db:
        records = db.scalars(select(IntegrationSetting).where(IntegrationSetting.key.in_(IIKO_LABELS.values()))).all()
    from app.security import decrypt_setting

    values = {record.key: decrypt_setting(record.encrypted_value) for record in records}
    if len(values) != 3:
        raise SystemExit("iiko: сохранены не все обязательные параметры")
    today = date.today()
    async with IikoClient(values["iiko_api_login"], values["iiko_app_id"], values["iiko_client_secret"]) as client:
        result = await client.diagnose(today - timedelta(days=1), today)
    print(f"iiko: организаций={len(result.organizations)}, терминалов={result.terminal_groups}, меню={len(result.external_menus)}, стоп-листов={result.stop_list_groups}, заказов={len(result.orders)}, ошибок={len(result.errors)}")
    for error in result.errors:
        print(f"iiko error: {error}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "import-iiko":
        import_iiko(sys.stdin.read())
    elif command == "diagnose-iiko":
        asyncio.run(diagnose())
    else:
        raise SystemExit("Команды: import-iiko, diagnose-iiko")
