from __future__ import annotations

import asyncio
import base64
import io
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class TtsProviderPreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.current = {
            "id": "mimo-test",
            "type": "mimo_tts_api",
            "provider_type": "text_to_speech",
            "enable": True,
            "api_key": "private-preview-key",
            "api_base": "https://example.test/v1",
            "model": "mimo-v2.5-tts",
            "mimo-tts-voice": "冰糖",
            "mimo-tts-style-prompt": "旧风格",
            "mimo-tts-format": "mp3",
        }
        self.manager = SimpleNamespace(
            get_provider_config_by_id=lambda _id: (
                deepcopy(self.current) if _id == "mimo-test" else None
            ),
            get_merged_provider_config=lambda config: deepcopy(config),
            update_provider=AsyncMock(),
            create_provider=AsyncMock(),
        )
        self.api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        self.api.plugin = SimpleNamespace(
            context=SimpleNamespace(provider_manager=self.manager)
        )
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        reference_root = patch(
            "astrbot.core.provider.sources.mimo_tts_reference.get_astrbot_data_path",
            return_value=self.temp.name,
        )
        reference_root.start()
        self.addCleanup(reference_root.stop)
        self.audio = Path(self.temp.name) / "preview.wav"
        self.audio.write_bytes(b"RIFF-test-audio")
        self.provider = SimpleNamespace(
            get_audio=AsyncMock(return_value=str(self.audio)), terminate=AsyncMock()
        )

    async def preview(self, **overrides):
        payload = {
            "provider_id": "mimo-test",
            "text": "试听一下新声音。",
            "config": {
                "values": {
                    "model": "mimo-v2.5-tts-voicedesign",
                    "mimo-tts-style-prompt": "清亮自然，语速适中。",
                    "api_key": "",
                }
            },
        }
        payload.update(overrides)
        req = SimpleNamespace(get_json=AsyncMock(return_value=payload))
        with (
            patch("astrbot_plugin_private_companion.page_api.request", req),
            patch(
                "astrbot.core.provider.sources.mimo_tts_api_source.ProviderMiMoTTSAPI",
                return_value=self.provider,
            ) as constructor,
        ):
            result = await self.api.preview_tts_provider_config()
        return result, constructor

    async def test_unsaved_design_returns_audio_without_saving_or_mutating(self):
        original = deepcopy(self.current)
        result, constructor = await self.preview()
        self.assertTrue(result["success"])
        data = result["data"]
        self.assertEqual(base64.b64decode(data["audio_base64"]), b"RIFF-test-audio")
        self.assertEqual(data["mime_type"], "audio/wav")
        self.assertTrue(data["preview_only"])
        config = constructor.call_args.args[0]
        self.assertEqual(config["model"], "mimo-v2.5-tts-voicedesign")
        self.assertEqual(config["api_key"], "private-preview-key")
        self.assertEqual(config["mimo-tts-format"], "wav")
        self.assertEqual(config["mimo-tts-style-prompt"], "清亮自然，语速适中。")
        self.provider.get_audio.assert_awaited_once_with("试听一下新声音。")
        self.provider.terminate.assert_awaited_once()
        self.assertFalse(self.audio.exists())
        self.assertEqual(self.current, original)
        self.manager.update_provider.assert_not_awaited()
        self.manager.create_provider.assert_not_awaited()
        self.assertNotIn("private-preview-key", repr(result))

    async def test_invalid_input_never_starts_synthesis(self):
        for overrides in (
            {"text": ""},
            {"text": "字" * 301},
            {"provider_id": "unknown"},
            {"config": {"values": {"model": "not-a-tts-model"}}},
        ):
            with self.subTest(overrides=overrides):
                result, constructor = await self.preview(**overrides)
                self.assertFalse(result["success"])
                constructor.assert_not_called()

    async def test_upstream_failure_is_redacted_and_client_is_closed(self):
        self.provider.get_audio.side_effect = RuntimeError(
            "private-preview-key upstream details"
        )
        result, _ = await self.preview()
        self.assertFalse(result["success"])
        self.assertNotIn("private-preview-key", repr(result))
        self.provider.terminate.assert_awaited_once()

    async def test_oversize_audio_is_cleaned_up(self):
        with self.audio.open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        result, _ = await self.preview()
        self.assertFalse(result["success"])
        self.assertFalse(self.audio.exists())
        self.provider.terminate.assert_awaited_once()

    async def test_timeout_closes_client_and_releases_preview_slot(self):
        self.provider.get_audio.side_effect = asyncio.TimeoutError()
        result, _ = await self.preview()
        self.assertFalse(result["success"])
        self.assertIn("超时", result["error"])
        self.provider.terminate.assert_awaited_once()
        self.assertFalse(self.api._tts_preview_lock.locked())

    async def test_cancellation_closes_client_and_releases_preview_slot(self):
        self.provider.get_audio.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.preview()
        self.provider.terminate.assert_awaited_once()
        self.assertFalse(self.api._tts_preview_lock.locked())

    async def test_busy_preview_is_rejected_without_queueing(self):
        self.api._tts_preview_lock = asyncio.Lock()
        await self.api._tts_preview_lock.acquire()
        try:
            result, constructor = await self.preview()
            self.assertFalse(result["success"])
            self.assertIn("正在生成", result["error"])
            constructor.assert_not_called()
        finally:
            self.api._tts_preview_lock.release()

    async def test_voice_design_requires_a_description(self):
        result, constructor = await self.preview(
            config={
                "values": {
                    "model": "mimo-v2.5-tts-voicedesign",
                    "mimo-tts-style-prompt": "",
                }
            }
        )
        self.assertFalse(result["success"])
        constructor.assert_not_called()

    def test_mimo_models_are_selectable_in_existing_editor(self):
        fields = self.api._tts_provider_schema_bundle()["by_type"]["mimo_tts_api"][
            "fields"
        ]
        model = next(f for f in fields if f["key"] == "model")
        self.assertEqual(
            [v["value"] for v in model["options"]],
            ["mimo-v2.5-tts", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"],
        )

    async def test_upload_saves_only_local_sample_not_provider(self):
        from astrbot.core.provider.sources.mimo_tts_reference import read_reference

        stream = io.BytesIO()
        with wave.open(stream, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x01\x00" * 8000 * 6)
        data = stream.getvalue()
        original = deepcopy(self.current)
        req = SimpleNamespace(get_json=AsyncMock(return_value={
            "provider_id": "mimo-test", "audio_base64": base64.b64encode(data).decode(),
        }))
        with patch("astrbot_plugin_private_companion.page_api.request", req):
            result = await self.api.upload_tts_reference_audio()
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["duration_seconds"], 6.0)
        self.assertEqual(read_reference(result["data"]["reference_id"]), data)
        self.assertEqual(self.current, original)
        self.manager.update_provider.assert_not_awaited()
        self.assertNotIn("audio_base64", result["data"])

    async def test_upload_rejects_bad_audio_and_unknown_provider(self):
        for payload in (
            {"provider_id": "mimo-test", "audio_base64": "invalid!"},
            {"provider_id": "mimo-test", "audio_base64": base64.b64encode(b"not-wav").decode()},
            {"provider_id": "unknown", "audio_base64": "anything"},
            [],
        ):
            with self.subTest(payload=payload):
                req = SimpleNamespace(get_json=AsyncMock(return_value=payload))
                with patch("astrbot_plugin_private_companion.page_api.request", req):
                    result = await self.api.upload_tts_reference_audio()
                self.assertFalse(result["success"])
                self.manager.update_provider.assert_not_awaited()

    async def test_clone_preview_uses_local_reference_without_saving(self):
        from astrbot.core.provider.sources.mimo_tts_reference import store_reference

        stream = io.BytesIO()
        with wave.open(stream, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x01\x00" * 8000 * 6)
        reference_id = store_reference(stream.getvalue())
        result, constructor = await self.preview(config={"values": {
            "model": "mimo-v2.5-tts-voiceclone", "mimo-tts-reference-audio": reference_id,
            "mimo-tts-style-prompt": "自然俏皮", "mimo-tts-dialect": "四川话",
        }})
        self.assertTrue(result["success"])
        config = constructor.call_args.args[0]
        self.assertEqual(config["mimo-tts-reference-audio"], reference_id)
        self.assertEqual(config["mimo-tts-style-prompt"], "自然俏皮")
        self.manager.update_provider.assert_not_awaited()

    def test_clone_save_requires_valid_stored_reference(self):
        with self.assertRaisesRegex(ValueError, "参考"):
            self.api._normalized_tts_provider_update(self.current, {"values": {
                "model": "mimo-v2.5-tts-voiceclone", "mimo-tts-reference-audio": "../secret.wav",
            }}, self.api._tts_provider_schema_bundle())


if __name__ == "__main__":
    unittest.main()
