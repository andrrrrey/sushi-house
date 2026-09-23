import asyncio
from array import array
import base64
import binascii
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
import logging
import json
import os
import re
import struct
import uuid

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.iiko import IikoOrderSnapshot
from app.models import IntegrationSetting, TestCall
from app.security import decrypt_setting
from app.settings_catalog import SETTINGS_BY_KEY


logger = logging.getLogger(__name__)

AUDIO_TYPE_PCM_8K = 0x10
AUDIO_FRAME_BYTES = 320
ACTIVE_CALL_STATES = ("dialing", "connected")
YANDEX_REQUIRED_KEYS = (
    "yandex_api_key",
    "yandex_folder_id",
    "yandex_gpt_model",
    "yandex_voice",
    "yandex_system_prompt",
)


class RealtimeBridgeError(RuntimeError):
    pass


def audio_socket_packet(message_type: int, payload: bytes = b"") -> bytes:
    if len(payload) > 65535:
        raise ValueError("AudioSocket payload is too large")
    return bytes((message_type,)) + struct.pack(">H", len(payload)) + payload


def pcm_rms(payload: bytes) -> int:
    samples = array("h")
    samples.frombytes(payload[: len(payload) - (len(payload) % 2)])
    if not samples:
        return 0
    return round((sum(sample * sample for sample in samples) / len(samples)) ** 0.5)


def downsample_pcm_16k_to_8k(payload: bytes) -> bytes:
    samples = array("h")
    samples.frombytes(payload[: len(payload) - (len(payload) % 4)])
    output = array("h")
    for index in range(0, len(samples), 2):
        output.append(round((samples[index] + samples[index + 1]) / 2))
    return output.tobytes()


def fast_confirmation_response(text: str) -> str | None:
    normalized = " ".join(text.lower().replace("ё", "е").split())
    words = set(re.findall(r"[\w-]+", normalized))
    negative_phrases = ("не подтверждаю", "неверно", "не верно", "ошибка")
    positive_phrases = ("подтверждаю", "все верно", "все правильно")
    if "нет" in words or any(phrase in normalized for phrase in negative_phrases):
        return "Понял. Скажите, пожалуйста, что именно в заказе или адресе указано неверно."
    if {"да", "верно"} & words or any(phrase in normalized for phrase in positive_phrases):
        return "Спасибо. Ваше подтверждение зафиксировано только в тестовом журнале и не отправлено в iiko."
    return None


class UtteranceDetector:
    """Local VAD for 20 ms, 8 kHz PCM16 telephony frames."""

    def __init__(
        self,
        threshold: int = 350,
        silence_frames: int = 25,
        minimum_frames: int = 10,
        maximum_frames: int = 1250,
        pre_roll_frames: int = 10,
    ):
        self.threshold = threshold
        self.silence_frames = silence_frames
        self.minimum_frames = minimum_frames
        self.maximum_frames = maximum_frames
        self.pre_roll = deque(maxlen=pre_roll_frames)
        self.frames: list[bytes] = []
        self.trailing_silence = 0
        self.started = False

    def reset(self) -> None:
        self.pre_roll.clear()
        self.frames.clear()
        self.trailing_silence = 0
        self.started = False

    def feed(self, payload: bytes) -> bytes | None:
        loud = pcm_rms(payload) >= self.threshold
        if not self.started:
            self.pre_roll.append(payload)
            if not loud:
                return None
            self.started = True
            self.frames = list(self.pre_roll)
            self.trailing_silence = 0
            return None

        self.frames.append(payload)
        self.trailing_silence = 0 if loud else self.trailing_silence + 1
        complete = (
            len(self.frames) >= self.maximum_frames
            or (len(self.frames) >= self.minimum_frames and self.trailing_silence >= self.silence_frames)
        )
        if not complete:
            return None
        utterance = b"".join(self.frames)
        self.reset()
        return utterance


def load_voice_settings() -> dict[str, str]:
    keys = (*YANDEX_REQUIRED_KEYS, "yandex_voice_emotion", "yandex_voice_speed", "mango_test_phone")
    with SessionLocal() as db:
        records = db.scalars(select(IntegrationSetting).where(IntegrationSetting.key.in_(keys))).all()
    values = {record.key: decrypt_setting(record.encrypted_value) for record in records}
    for key in ("yandex_gpt_model", "yandex_voice", "yandex_voice_emotion", "yandex_voice_speed", "yandex_system_prompt"):
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


class YandexVoiceClient:
    STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
    TTS_URL = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"
    LLM_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def __init__(
        self,
        settings: dict[str, str],
        http_client: httpx.AsyncClient | None = None,
        extra_instructions: str = "",
    ):
        self.api_key = settings["yandex_api_key"]
        self.folder_id = settings["yandex_folder_id"]
        self.model = settings["yandex_gpt_model"]
        self.voice = settings["yandex_voice"]
        self.emotion = settings.get("yandex_voice_emotion", "friendly")
        self.speed = settings.get("yandex_voice_speed", "1.0")
        self.system_prompt = f"{settings['yandex_system_prompt']}\n\n{extra_instructions}".strip()
        self.http = http_client or httpx.AsyncClient(
            headers={"Authorization": f"Api-Key {self.api_key}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def close(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _raise(response: httpx.Response, service: str) -> None:
        if response.is_success:
            return
        try:
            body = response.json()
            raw_error = body.get("error") or {}
            message = body.get("message") or body.get("error_message")
            if not message and isinstance(raw_error, dict):
                message = raw_error.get("message")
        except (ValueError, AttributeError):
            message = response.text[:300]
        raise RealtimeBridgeError(f"{service}: HTTP {response.status_code}: {message or 'ошибка API'}")

    async def recognize(self, pcm_8k: bytes) -> str:
        response = await self.http.post(
            self.STT_URL,
            params={
                "lang": "ru-RU",
                "topic": "general",
                "format": "lpcm",
                "sampleRateHertz": "8000",
            },
            content=pcm_8k,
            headers={"Content-Type": "application/octet-stream"},
        )
        self._raise(response, "Yandex SpeechKit STT")
        return str(response.json().get("result") or "").strip()

    async def complete(self, history: list[dict[str, str]]) -> str:
        model_uri = self.model if self.model.startswith("gpt://") else f"gpt://{self.folder_id}/{self.model}"
        response = await self.http.post(
            self.LLM_URL,
            json={
                "modelUri": model_uri,
                "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": "120"},
                "messages": [{"role": "system", "text": self.system_prompt}, *history[-12:]],
            },
        )
        self._raise(response, "YandexGPT")
        try:
            return str(response.json()["result"]["alternatives"][0]["message"]["text"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RealtimeBridgeError("YandexGPT вернул ответ неизвестного формата") from exc

    async def synthesize(self, text: str) -> bytes:
        hints = [{"voice": self.voice}, {"speed": self.speed}, {"volume": "0.85"}]
        if self.emotion and self.emotion != "auto":
            hints.append({"role": self.emotion})
        response = await self.http.post(
            self.TTS_URL,
            json={
                "text": text[:5000],
                "hints": hints,
                "outputAudioSpec": {
                    "rawAudio": {"audioEncoding": "LINEAR16_PCM", "sampleRateHertz": "16000"},
                },
                "loudnessNormalizationType": "MAX_PEAK",
                "unsafeMode": True,
            },
        )
        self._raise(response, "Yandex SpeechKit TTS")
        try:
            parsed = response.json()
            payloads = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            try:
                payloads = [json.loads(line) for line in response.text.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise RealtimeBridgeError("Yandex SpeechKit TTS вернул ответ неизвестного формата") from exc
        chunks = []
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            result = payload.get("result", payload)
            encoded = (result.get("audioChunk") or {}).get("data") if isinstance(result, dict) else None
            if encoded:
                try:
                    chunks.append(base64.b64decode(encoded, validate=True))
                except (ValueError, binascii.Error) as exc:
                    raise RealtimeBridgeError("Yandex SpeechKit TTS вернул повреждённый аудиофрагмент") from exc
        if not chunks:
            raise RealtimeBridgeError("Yandex SpeechKit TTS не вернул аудио")
        return downsample_pcm_16k_to_8k(b"".join(chunks))


class AudioBridgeManager:
    def __init__(self, host: str = "0.0.0.0", port: int = 9092):
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._watchdogs: dict[str, asyncio.Task] = {}
        self._orders: dict[str, IikoOrderSnapshot] = {}

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
        self._orders.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def prepare_call(self, call_id: str, order: IikoOrderSnapshot) -> None:
        self._orders[call_id] = order

    def clear_call(self, call_id: str) -> None:
        self._orders.pop(call_id, None)

    def watch_dialing(self, call_id: str, timeout_seconds: int = 70) -> None:
        async def expire():
            try:
                await asyncio.sleep(timeout_seconds)
                with SessionLocal() as db:
                    record = db.get(TestCall, call_id)
                    state = record.status if record else None
                if state == "dialing":
                    self.clear_call(call_id)
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
                raise RealtimeBridgeError("AudioSocket не передал корректный UUID звонка")
            call_id = str(uuid.UUID(bytes=payload))
            with SessionLocal() as db:
                record = db.get(TestCall, call_id)
                allowed = bool(record and record.status == "dialing")
            if not allowed:
                raise RealtimeBridgeError("Неизвестный или уже завершённый тестовый звонок")
            order = self._orders.pop(call_id, None)
            if not order:
                raise RealtimeBridgeError("Для тестового звонка не подготовлен заказ iiko")
            watchdog = self._watchdogs.pop(call_id, None)
            if watchdog:
                watchdog.cancel()
            update_test_call(call_id, status="connected", connected_at=datetime.now(UTC), error="")
            await self._run_yandex_bridge(call_id, order, reader, writer)
            update_test_call(call_id, status="completed", finished_at=datetime.now(UTC))
        except asyncio.IncompleteReadError:
            if call_id:
                update_test_call(call_id, status="completed", finished_at=datetime.now(UTC))
        except Exception as exc:
            logger.warning("Yandex voice bridge failed for %s: %s", call_id or "unknown", exc)
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

    async def _run_yandex_bridge(
        self,
        call_id: str,
        order: IikoOrderSnapshot,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        settings = load_voice_settings()
        missing = [key for key in YANDEX_REQUIRED_KEYS if not settings.get(key)]
        if missing:
            raise RealtimeBridgeError("Не заполнены настройки Yandex Cloud")
        client = YandexVoiceClient(settings, extra_instructions=order.prompt_context())
        history: list[dict[str, str]] = []
        detector = UtteranceDetector()
        incoming: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=500)
        reader_task = asyncio.create_task(self._audio_socket_to_queue(reader, incoming))
        try:
            greeting = order.greeting()
            append_transcript(call_id, "assistant", greeting)
            await self._play_pcm(writer, await client.synthesize(greeting))
            if self._discard_incoming(incoming):
                return
            async with asyncio.timeout(5 * 60):
                while True:
                    payload = await incoming.get()
                    if payload is None:
                        return
                    utterance = detector.feed(payload)
                    if utterance is None:
                        continue
                    recognized = await client.recognize(utterance)
                    if not recognized:
                        continue
                    append_transcript(call_id, "user", recognized)
                    history.append({"role": "user", "text": recognized})
                    answer = fast_confirmation_response(recognized) or await client.complete(history)
                    if not answer:
                        continue
                    history.append({"role": "assistant", "text": answer})
                    append_transcript(call_id, "assistant", answer)
                    if self._discard_incoming(incoming):
                        return
                    detector.reset()
                    await self._play_pcm(writer, await client.synthesize(answer))
                    if self._discard_incoming(incoming):
                        return
                    detector.reset()
        finally:
            reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await reader_task
            await client.close()

    @staticmethod
    async def _play_pcm(writer: asyncio.StreamWriter, pcm: bytes) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        for frame_number, offset in enumerate(range(0, len(pcm), AUDIO_FRAME_BYTES), start=1):
            frame = pcm[offset:offset + AUDIO_FRAME_BYTES]
            if len(frame) < AUDIO_FRAME_BYTES:
                frame += b"\x00" * (AUDIO_FRAME_BYTES - len(frame))
            writer.write(audio_socket_packet(AUDIO_TYPE_PCM_8K, frame))
            if frame_number % 5 == 0:
                await writer.drain()
            deadline += 0.02
            delay = deadline - loop.time()
            await asyncio.sleep(delay if delay > 0 else 0)
        await writer.drain()

    async def _audio_socket_to_queue(
        self,
        reader: asyncio.StreamReader,
        queue: asyncio.Queue[bytes | None],
    ) -> None:
        try:
            while True:
                message_type, payload = await self._read_packet(reader)
                if message_type in (0x00, 0xFF):
                    await queue.put(None)
                    return
                if message_type != AUDIO_TYPE_PCM_8K or not payload:
                    continue
                if queue.full():
                    with suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                queue.put_nowait(payload)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            # Asterisk normally closes the AudioSocket when the call ends.  Make
            # the bridge consume a regular end marker instead of surfacing a
            # transport exception and incorrectly marking the call as failed.
            with suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    @staticmethod
    def _discard_incoming(queue: asyncio.Queue[bytes | None]) -> bool:
        closed = False
        while True:
            try:
                if queue.get_nowait() is None:
                    closed = True
            except asyncio.QueueEmpty:
                return closed

    @staticmethod
    async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
        header = await reader.readexactly(3)
        length = struct.unpack(">H", header[1:])[0]
        payload = await reader.readexactly(length) if length else b""
        return header[0], payload


audio_bridge = AudioBridgeManager(
    host=os.getenv("AUDIO_SOCKET_HOST", "0.0.0.0"),
    port=int(os.getenv("AUDIO_SOCKET_PORT", "9092")),
)
