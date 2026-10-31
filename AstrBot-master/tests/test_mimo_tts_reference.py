import asyncio
import base64
import importlib
import io
import wave

import pytest

from astrbot.core.provider.sources.mimo_tts_api_source import ProviderMiMoTTSAPI


def wav_bytes(seconds=6):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x01\x00" * int(8000 * seconds))
    return stream.getvalue()


@pytest.fixture
def references(tmp_path, monkeypatch):
    module = importlib.import_module("astrbot.core.provider.sources.mimo_tts_reference")
    monkeypatch.setattr(module, "get_astrbot_data_path", lambda: str(tmp_path))
    return module


def test_reference_roundtrip_is_content_addressed_and_not_duplicated(references):
    data = wav_bytes()
    reference_id = references.store_reference(data)
    assert reference_id == references.store_reference(data)
    assert references.read_reference(reference_id) == data
    assert references.validate_reference(data) == 6.0


@pytest.mark.parametrize(
    "data",
    [b"not-wav", wav_bytes(4.9), wav_bytes(30.1)],
    ids=["invalid-wav", "too-short", "too-long"],
)
def test_reference_rejects_invalid_format_and_duration(references, data):
    with pytest.raises(ValueError):
        references.store_reference(data)


def test_reference_rejects_oversize_base64(references):
    with pytest.raises(ValueError, match="10MB"):
        references.store_reference(b"x" * 7_500_001)


@pytest.mark.parametrize(
    "reference_id", ["../secret.wav", "C:/secret.wav", "https://a/b.wav", ""]
)
def test_reference_rejects_arbitrary_paths(references, reference_id):
    with pytest.raises(ValueError):
        references.read_reference(reference_id)


def test_missing_reference_does_not_fall_back_to_preset(references):
    provider = ProviderMiMoTTSAPI(
        {
            "id": "test",
            "model": "mimo-v2.5-tts-voiceclone",
            "mimo-tts-reference-audio": "a" * 64 + ".wav",
        },
        {},
    )
    try:
        with pytest.raises(ValueError, match="参考"):
            provider._build_payload("你好")
    finally:
        asyncio.run(provider.terminate())


def test_clone_reuses_reference_in_every_request_and_keeps_instructions_separate(
    references,
):
    data = wav_bytes()
    reference_id = references.store_reference(data)
    provider = ProviderMiMoTTSAPI(
        {
            "id": "xiaoqi",
            "model": "mimo-v2.5-tts-voiceclone",
            "mimo-tts-reference-audio": reference_id,
            "mimo-tts-style-prompt": "俏皮，语速适中",
            "mimo-tts-dialect": "四川话",
            "mimo-tts-seed-text": "",
        },
        {},
    )
    other = ProviderMiMoTTSAPI(
        {
            "id": "wanning",
            "model": "mimo-v2.5-tts",
            "mimo-tts-voice": "茉莉",
            "mimo-tts-seed-text": "",
        },
        {},
    )
    try:
        for text in ("你好", "再说一句"):
            payload = provider._build_payload(text)
            assert (
                payload["audio"]["voice"]
                == "data:audio/wav;base64," + base64.b64encode(data).decode()
            )
            assert payload["messages"] == [
                {"role": "user", "content": "俏皮，语速适中\n四川话"},
                {"role": "assistant", "content": text},
            ]
        assert other._build_payload("你好")["audio"]["voice"] == "茉莉"
    finally:
        asyncio.run(provider.terminate())
        asyncio.run(other.terminate())
