import asyncio
from array import array
import base64
from io import BytesIO
import json
import wave

import httpx

from app.realtime import (
    UtteranceDetector,
    YandexVoiceClient,
    audio_socket_packet,
    downsample_pcm_16k_to_8k,
    fast_confirmation_response,
    pcm16_wav,
    pcm_rms,
)


def pcm_frame(level: int) -> bytes:
    return array("h", [level] * 160).tobytes()


def test_audio_socket_packet_uses_big_endian_length():
    assert audio_socket_packet(0x10, b"abcd") == b"\x10\x00\x04abcd"


def test_pcm_rms_distinguishes_silence_from_speech_level():
    assert pcm_rms(pcm_frame(0)) == 0
    assert pcm_rms(pcm_frame(1200)) == 1200


def test_downsample_averages_16k_sample_pairs():
    source = array("h", [1000, 2000, -1000, -3000]).tobytes()
    assert downsample_pcm_16k_to_8k(source) == array("h", [1500, -2000]).tobytes()


def test_pcm_preview_is_browser_playable_wav():
    source = array("h", [1000, -1000, 500, -500]).tobytes()
    with wave.open(BytesIO(pcm16_wav(source)), "rb") as stream:
        assert stream.getnchannels() == 1
        assert stream.getsampwidth() == 2
        assert stream.getframerate() == 8000
        assert stream.readframes(4) == source


def test_common_confirmation_answers_skip_language_model_delay():
    assert "зафиксировано" in fast_confirmation_response("Да, всё верно")
    assert "что именно" in fast_confirmation_response("Нет, не подтверждаю")
    assert fast_confirmation_response("У меня вопрос по заказу") is None


def test_utterance_detector_emits_pcm_after_trailing_silence():
    detector = UtteranceDetector(
        threshold=350,
        silence_frames=3,
        minimum_frames=4,
        maximum_frames=50,
        pre_roll_frames=2,
    )

    assert detector.feed(pcm_frame(0)) is None
    assert detector.feed(pcm_frame(900)) is None
    assert detector.feed(pcm_frame(900)) is None
    assert detector.feed(pcm_frame(0)) is None
    assert detector.feed(pcm_frame(0)) is None
    utterance = detector.feed(pcm_frame(0))

    assert utterance is not None
    assert len(utterance) == 6 * 320
    assert detector.started is False


def test_utterance_detector_enforces_maximum_duration():
    detector = UtteranceDetector(
        threshold=350,
        silence_frames=10,
        minimum_frames=1,
        maximum_frames=3,
        pre_roll_frames=1,
    )

    assert detector.feed(pcm_frame(900)) is None
    assert detector.feed(pcm_frame(900)) is None
    assert detector.feed(pcm_frame(900)) is not None


def test_yandex_voice_client_uses_telephony_audio_and_model_uri():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("stt:recognize"):
            return httpx.Response(200, json={"result": "Да, подтверждаю"})
        if request.url.path.endswith("completion"):
            return httpx.Response(200, json={
                "result": {"alternatives": [{"message": {"text": "Спасибо, заказ подтверждён."}}]},
            })
        return httpx.Response(200, json={
            "result": {"audioChunk": {"data": base64.b64encode(array("h", [1000, 2000]).tobytes()).decode()}},
        })

    async def run():
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Api-Key test-key"},
        )
        client = YandexVoiceClient({
            "yandex_api_key": "test-key",
            "yandex_folder_id": "folder-1",
            "yandex_gpt_model": "yandexgpt/latest",
            "yandex_voice": "marina",
            "yandex_voice_emotion": "friendly",
            "yandex_voice_speed": "1.2",
            "yandex_system_prompt": "Говори кратко",
        }, http_client=http)
        assert await client.recognize(b"pcm") == "Да, подтверждаю"
        assert await client.complete([{"role": "user", "text": "Да"}]) == "Спасибо, заказ подтверждён."
        assert await client.synthesize("Спасибо") == array("h", [1500]).tobytes()
        await client.close()

    asyncio.run(run())

    stt = requests[0]
    assert stt.url.params["format"] == "lpcm"
    assert stt.url.params["sampleRateHertz"] == "8000"
    llm_payload = json.loads(requests[1].content)
    assert llm_payload["modelUri"] == "gpt://folder-1/yandexgpt/latest"
    assert llm_payload["messages"][0] == {"role": "system", "text": "Говори кратко"}
    tts_payload = json.loads(requests[2].content)
    assert {"voice": "marina"} in tts_payload["hints"]
    assert {"role": "friendly"} in tts_payload["hints"]
    assert {"speed": "1.2"} in tts_payload["hints"]
    assert {"volume": "0.85"} in tts_payload["hints"]
    assert tts_payload["outputAudioSpec"]["rawAudio"]["sampleRateHertz"] == "16000"
    assert tts_payload["loudnessNormalizationType"] == "MAX_PEAK"
    assert tts_payload["unsafeMode"] is True
