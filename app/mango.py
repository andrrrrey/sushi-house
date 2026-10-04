from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Any

import httpx


MAX_EVENT_BYTES = 256 * 1024


class MangoEventError(ValueError):
    pass


class MangoApiError(RuntimeError):
    pass


def verify_signature(api_key: str, api_salt: str, received_key: str, raw_json: str, signature: str) -> bool:
    if not received_key or not signature or not hmac.compare_digest(received_key, api_key):
        return False
    expected = hashlib.sha256(f"{api_key}{raw_json}{api_salt}".encode()).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def sign_payload(api_key: str, api_salt: str, raw_json: str) -> str:
    return hashlib.sha256(f"{api_key}{raw_json}{api_salt}".encode()).hexdigest()


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("8"):
        return f"7{digits[1:]}"
    return digits


def phones_match(left: str, right: str) -> bool:
    normalized_left = normalize_phone(left)
    normalized_right = normalize_phone(right)
    return bool(normalized_left and normalized_left == normalized_right)


@dataclass(frozen=True)
class ParsedCallEvent:
    entry_id: str
    call_id: str
    sequence: int
    call_state: str
    location: str
    from_number: str
    to_number: str
    to_extension: str
    line_number: str
    disconnect_reason: str
    event_timestamp: int | None


def parse_call_event(raw_json: str) -> ParsedCallEvent:
    if len(raw_json.encode()) > MAX_EVENT_BYTES:
        raise MangoEventError("Event is too large")
    try:
        payload: Any = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise MangoEventError("Invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MangoEventError("Event JSON must be an object")
    call_id = str(payload.get("call_id") or "").strip()
    if not call_id:
        raise MangoEventError("call_id is required")
    from_data = payload.get("from") if isinstance(payload.get("from"), dict) else {}
    to_data = payload.get("to") if isinstance(payload.get("to"), dict) else {}
    try:
        sequence = int(payload.get("seq") or 0)
    except (TypeError, ValueError) as exc:
        raise MangoEventError("seq must be an integer") from exc
    timestamp = payload.get("timestamp")
    try:
        timestamp = int(timestamp) if timestamp is not None else None
    except (TypeError, ValueError):
        timestamp = None
    return ParsedCallEvent(
        entry_id=str(payload.get("entry_id") or "")[:128],
        call_id=call_id[:128],
        sequence=sequence,
        call_state=str(payload.get("call_state") or "Unknown")[:40],
        location=str(payload.get("location") or "")[:40],
        from_number=str(from_data.get("number") or "")[:160],
        to_number=str(to_data.get("number") or "")[:160],
        to_extension=str(to_data.get("extension") or "")[:40],
        line_number=str(to_data.get("line_number") or "")[:160],
        disconnect_reason=str(payload.get("disconnect_reason") or "")[:40],
        event_timestamp=timestamp,
    )


def should_route_test_call(
    event: ParsedCallEvent,
    *,
    enabled: bool,
    test_phone: str,
    target_extension: str,
) -> bool:
    if not enabled or not phones_match(event.from_number, test_phone):
        return False
    if event.call_state.casefold() != "appeared":
        return False
    if event.to_extension.strip() == target_extension.strip():
        return False
    location = event.location.casefold()
    return location == "queue" or location == "abonent" or location.startswith("ivr")


class MangoClient:
    def __init__(
        self,
        api_key: str,
        api_salt: str,
        *,
        base_url: str = "https://app.mango-office.ru/vpbx/",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.api_salt = api_salt
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(4.0, connect=2.0),
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def route_call(self, call_id: str, to_number: str, command_id: str) -> None:
        payload = {
            "command_id": command_id,
            "call_id": call_id,
            "to_number": to_number,
        }
        raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            response = await self.client.post(
                "commands/route",
                data={
                    "vpbx_api_key": self.api_key,
                    "sign": sign_payload(self.api_key, self.api_salt, raw_json),
                    "json": raw_json,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()[:300]
            raise MangoApiError(f"Mango route HTTP {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise MangoApiError(f"Mango route request failed: {exc}") from exc


def parse_route_result(raw_json: str) -> tuple[str, int]:
    if len(raw_json.encode()) > MAX_EVENT_BYTES:
        raise MangoEventError("Result is too large")
    try:
        payload = json.loads(raw_json)
        command_id = str(payload.get("command_id") or "").strip()
        result = int(payload.get("result"))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MangoEventError("Invalid route result") from exc
    if not command_id:
        raise MangoEventError("command_id is required")
    return command_id[:128], result
