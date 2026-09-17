from array import array

from app.realtime import Pcm24kTo8k, audio_socket_packet, build_session_update, upsample_pcm_8k_to_24k


def test_audio_socket_packet_uses_big_endian_length():
    assert audio_socket_packet(0x10, b"abcd") == b"\x10\x00\x04abcd"


def test_pcm_conversion_preserves_duration_and_approximate_level():
    source = array("h", [1200] * 160).tobytes()
    upsampled = upsample_pcm_8k_to_24k(source)
    assert len(upsampled) == len(source) * 3

    downsampled = Pcm24kTo8k().convert(upsampled)
    assert downsampled == source


def test_realtime_session_is_audio_only_with_server_vad():
    event = build_session_update("gpt-realtime-2", "marin", "Говори кратко")
    session = event["session"]

    assert event["type"] == "session.update"
    assert session["output_modalities"] == ["audio"]
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert session["audio"]["input"]["turn_detection"]["type"] == "server_vad"
    assert session["audio"]["output"]["voice"] == "marin"
