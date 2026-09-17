import asyncio
from array import array
import base64
from contextlib import suppress
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
import struct
from urllib.parse import quote
import uuid

from sqlalchemy import select
import websockets

from app.db import SessionLocal
from app.models import IntegrationSetting, TestCall
from app.security import decrypt_setting
from app.settings_catalog import SETTINGS_BY_KEY


logger = logging.getLogger(__name__)

AUDIO_TYPE_PCM_8K = 0x10
AUDIO_FRAME_BYTES = 320
ACTIVE_CALL_STATES = ("dialing", "connected")
OPENAI_REQUIRED_KEYS = (
    "openai_api_key",
    "openai_realtime_model",
    "openai_voice",
    "openai_system_prompt",
)


class RealtimeBridgeError(RuntimeError):
    pass


def audio_socket_packet(message_type: int, payload: bytes = b"") -> bytes:
    if len(payload) > 65535:
        raise ValueError("AudioSocket payload is too large")
    return bytes((message_type,)) + struct.pack(">H", len(payload)) + payload


def upsample_pcm_8k_to_24k(payload: bytes) -> bytes:
    """Convert little-endian mono PCM16 from 8 kHz to 24 kHz."""
    samples = array("h")
    samples.frombytes(payload[: len(payload) - (len(payload) % 2)])
    if not samples:
        return b""
    output = array("h")
    for index, current in enumerate(samples):
        following = samples[index + 1] if index + 1 < len(samples) else current
        output.extend((current, round((2 * current + following) / 3), round((current + 2 * following) / 3)))
    return output.tobytes()


class Pcm24kTo8k:
    def __init__(self):
        self._remainder = bytearray()

    def convert(self, payload: bytes) -> bytes:
        self._remainder.extend(payload)
        usable_bytes = len(self._remainder) - (len(self._remainder) % 6)
        if not usable_bytes:
            return b""
        samples = array("h")
        samples.frombytes(bytes(self._remainder[:usable_bytes]))
        del self._remainder[:usable_bytes]
        output = array("h")
        for index in range(0, len(samples), 3):
            output.append(round(sum(samples[index:index + 3]) / 3))
        return output.tobytes()


def build_session_update(model: str, voice: str, instructions: str) -> dict:
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": model,
            "output_modalities": ["audio"],
            "instructions": instructions,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 600,
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": voice,
                },
            },
        },
    }


def load_realtime_settings() -> dict[str, str]:
    keys = (*OPENAI_REQUIRED_KEYS, "mango_test_phone")
    with SessionLocal() as db:
        records = db.scalars(select(IntegrationSetting).where(IntegrationSetting.key.in_(keys))).all()
    values = {record.key: decrypt_setting(record.encrypted_value) for record in records}
    for key in ("openai_realtime_model", "openai_voice", "openai_system_prompt"):
        values.setdefault(key, SETTINGS_BY_KEY[key].default)
    return values


def update_test_call(call_id: str, **values) -> None:
    with SessionLocal.begin() as db:
        record = db.get(TestCall, call_id)
        if not record:
            return
        for key, value in values.items():
            setattr(record, key, value)


def append_transcript(call_id: str, speaker: str, text: str) -> None:
    text = text.strip()
    if not text:
        return
    with SessionLocal.begin() as db:
        record = db.get(TestCall, call_id)
        if not record:
            return
        prefix = "Робот" if speaker == "assistant" else "Абонент"
        line = f"{prefix}: {text}"
        record.transcript = f"{record.transcript}\n{line}".strip()[-12000:]


class AudioBridgeManager:
    def __init__(self, host: str = "0.0.0.0", port: int = 9092):
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._watchdogs: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        with SessionLocal.begin() as db:
            stale = db.scalars(select(TestCall).where(TestCall.status.in_(ACTIVE_CALL_STATES))).all()
            for record in stale:
                record.status = "interrupted"
                record.error = "Приложение было перезапущено во время тестового звонка"
                record.finished_at = datetime.now(UTC)
        self._server = await asyncio.start_server(self._handle_audio_socket, self.host, self.port)
        logger.info("AudioSocket bridge listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        for task in self._watchdogs.values():
            task.cancel()
        self._watchdogs.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def watch_dialing(self, call_id: str, timeout_seconds: int = 70) -> None:
        async def expire():
            try:
                await asyncio.sleep(timeout_seconds)
                with SessionLocal() as db:
                    record = db.get(TestCall, call_id)
                    state = record.status if record else None
                if state == "dialing":
                    update_test_call(
                        call_id,
                        status="not_answered",
                        error="Asterisk не получил ответ или не открыл аудиоканал",
                        finished_at=datetime.now(UTC),
                    )
            finally:
                self._watchdogs.pop(call_id, None)

        previous = self._watchdogs.pop(call_id, None)
        if previous:
            previous.cancel()
        self._watchdogs[call_id] = asyncio.create_task(expire())

    async def _handle_audio_socket(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        call_id = ""
        try:
            message_type, payload = await self._read_packet(reader)
            if message_type != 0x01 or len(payload) != 16:
                raise RealtimeBridgeError("AudioSocket did not provide a valid call UUID")
            call_id = str(uuid.UUID(bytes=payload))
            with SessionLocal() as db:
                record = db.get(TestCall, call_id)
                allowed = bool(record and record.status == "dialing")
            if not allowed:
                raise RealtimeBridgeError("Unknown or inactive test call")
            watchdog = self._watchdogs.pop(call_id, None)
            if watchdog:
                watchdog.cancel()
            update_test_call(call_id, status="connected", connected_at=datetime.now(UTC), error="")
            await self._run_openai_bridge(call_id, reader, writer)
            update_test_call(call_id, status="completed", finished_at=datetime.now(UTC))
        except asyncio.IncompleteReadError:
            if call_id:
                update_test_call(call_id, status="completed", finished_at=datetime.now(UTC))
        except Exception as exc:
            logger.warning("Realtime bridge failed for %s: %s", call_id or "unknown", exc)
            if call_id:
                update_test_call(
                    call_id,
                    status="failed",
                    error=str(exc)[:1000],
                    finished_at=datetime.now(UTC),
                )
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _run_openai_bridge(
        self,
        call_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        settings = load_realtime_settings()
        missing = [key for key in OPENAI_REQUIRED_KEYS if not settings.get(key)]
        if missing:
            raise RealtimeBridgeError("Не заполнены настройки OpenAI Realtime")
        model = settings["openai_realtime_model"]
        url = f"wss://api.openai.com/v1/realtime?model={quote(model, safe='-._')}"
        safety_id = hashlib.sha256(f"sushi-house-test:{call_id}".encode()).hexdigest()
        headers = {
            "Authorization": f"Bearer {settings['openai_api_key']}",
            "OpenAI-Safety-Identifier": safety_id,
        }
        async with websockets.connect(
            url,
            additional_headers=headers,
            open_timeout=15,
            close_timeout=5,
            max_size=8 * 1024 * 1024,
        ) as websocket:
            await websocket.send(json.dumps(build_session_update(
                model,
                settings["openai_voice"],
                settings["openai_system_prompt"],
            )))
            await self._wait_until_ready(websocket)
            await websocket.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": "Системное событие: абонент ответил на тестовый звонок. Начни разговор первым.",
                    }],
                },
            }))
            await websocket.send(json.dumps({"type": "response.create"}))
            outgoing_audio: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
            async with asyncio.timeout(5 * 60):
                tasks = [
                    asyncio.create_task(self._asterisk_to_openai(reader, websocket)),
                    asyncio.create_task(self._openai_to_queue(call_id, websocket, outgoing_audio)),
                    asyncio.create_task(self._queue_to_asterisk(writer, outgoing_audio)),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in pending:
                    with suppress(asyncio.CancelledError):
                        await task
                for task in done:
                    task.result()

    async def _wait_until_ready(self, websocket) -> None:
        for _ in range(20):
            event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
            if event.get("type") == "session.updated":
                return
            if event.get("type") == "error":
                error = event.get("error") or {}
                raise RealtimeBridgeError(f"OpenAI Realtime: {error.get('message', 'ошибка сессии')}")
        raise RealtimeBridgeError("OpenAI Realtime не подтвердил настройки сессии")

    async def _asterisk_to_openai(self, reader: asyncio.StreamReader, websocket) -> None:
        while True:
            message_type, payload = await self._read_packet(reader)
            if message_type in (0x00, 0xFF):
                return
            if message_type != AUDIO_TYPE_PCM_8K or not payload:
                continue
            pcm_24k = upsample_pcm_8k_to_24k(payload)
            await websocket.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm_24k).decode(),
            }))

    async def _openai_to_queue(self, call_id: str, websocket, queue: asyncio.Queue) -> None:
        assistant_transcript = []
        async for raw_message in websocket:
            event = json.loads(raw_message)
            event_type = event.get("type", "")
            if event_type == "response.output_audio.delta":
                await queue.put(base64.b64decode(event.get("delta", ""), validate=True))
            elif event_type == "response.output_audio_transcript.delta":
                assistant_transcript.append(str(event.get("delta") or ""))
            elif event_type == "response.output_audio_transcript.done":
                transcript = str(event.get("transcript") or "") or "".join(assistant_transcript)
                append_transcript(call_id, "assistant", transcript)
                assistant_transcript.clear()
            elif event_type == "conversation.item.input_audio_transcription.completed":
                append_transcript(call_id, "user", str(event.get("transcript") or ""))
            elif event_type == "input_audio_buffer.speech_started":
                self._clear_queue(queue)
            elif event_type == "error":
                error = event.get("error") or {}
                raise RealtimeBridgeError(f"OpenAI Realtime: {error.get('message', 'ошибка потока')}")
        await queue.put(None)

    async def _queue_to_asterisk(self, writer: asyncio.StreamWriter, queue: asyncio.Queue) -> None:
        converter = Pcm24kTo8k()
        buffer = bytearray()
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            buffer.extend(converter.convert(chunk))
            while len(buffer) >= AUDIO_FRAME_BYTES:
                frame = bytes(buffer[:AUDIO_FRAME_BYTES])
                del buffer[:AUDIO_FRAME_BYTES]
                writer.write(audio_socket_packet(AUDIO_TYPE_PCM_8K, frame))
                await writer.drain()
                await asyncio.sleep(0.02)

    @staticmethod
    async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
        header = await reader.readexactly(3)
        length = struct.unpack(">H", header[1:])[0]
        payload = await reader.readexactly(length) if length else b""
        return header[0], payload

    @staticmethod
    def _clear_queue(queue: asyncio.Queue) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return


audio_bridge = AudioBridgeManager(
    host=os.getenv("AUDIO_SOCKET_HOST", "0.0.0.0"),
    port=int(os.getenv("AUDIO_SOCKET_PORT", "9092")),
)
