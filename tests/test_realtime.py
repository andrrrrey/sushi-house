import asyncio
from array import array
import base64
import json

import httpx

from app.realtime import UtteranceDetector, YandexVoiceClient, audio_socket_packet, pcm_rms


def pcm_frame(level: int) -> bytes:
    return array("h", [level] * 160).tobytes()


def test_audio_socket_packet_uses_big_endian_length():
    assert audio_socket_packet(0x10, b"abcd") == b"\x10\x00\x04abcd"


def test_pcm_rms_distinguishes_silence_from_speech_level():
    assert pcm_rms(pcm_frame(0)) == 0
    assert pcm_rms(pcm_frame(1200)) == 1200


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
            "result": {"audioChunk": {"data": base64.b64encode(b"\x01\x02").decode()}},
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
        assert await client.synthesize("Спасибо") == b"\x01\x02"
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
    assert tts_payload["outputAudioSpec"]["rawAudio"]["sampleRateHertz"] == "8000"
    assert tts_payload["unsafeMode"] is True
