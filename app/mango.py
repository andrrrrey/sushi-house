from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Any


MAX_EVENT_BYTES = 256 * 1024


class MangoEventError(ValueError):
    pass


def verify_signature(api_key: str, api_salt: str, received_key: str, raw_json: str, signature: str) -> bool:
    if not received_key or not signature or not hmac.compare_digest(received_key, api_key):
        return False
    expected = hashlib.sha256(f"{api_key}{raw_json}{api_salt}".encode()).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


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
