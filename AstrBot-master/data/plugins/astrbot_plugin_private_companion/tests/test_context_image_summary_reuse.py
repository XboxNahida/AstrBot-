# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


def _harness() -> PrivateCompanionPlugin:
    plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
    plugin.enable_context_image_captioning = True
    plugin.context_image_caption_max_items = 12
    plugin.private_image_vision_max_chars = 2400
    plugin._recall_message_cache = {
        "m1": {
            "message_id": "m1",
            "scope": "group:g1",
            "sender_id": "u1",
            "sender_name": "小明",
            "text": "[图片]",
            "images": ["https://example.com/a.png"],
            "image_items": [{"source": "https://example.com/a.png", "tier": "url"}],
            "ts": 1,
        }
    }
    plugin._event_scope_key = lambda _event: "group:g1"
    plugin._cleanup_recall_message_cache = lambda: None
    plugin._recall_image_items_from_snapshot = lambda row: row.get("image_items", [])
    plugin._context_image_cached_summary = AsyncMock(return_value="图片类型：照片 可见内容：一只橘猫")
    plugin._caption_context_image_sources = AsyncMock(return_value="重新识别结果")
    return plugin


class ContextImageSummaryReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_image_reuses_cached_summary_and_attributes_sender(self) -> None:
        plugin = _harness()
        request = SimpleNamespace(contexts=[{"role": "user", "content": "[图片]"}])

        result = await plugin._enrich_request_context_image_placeholders(
            SimpleNamespace(message_str="那张图呢"), request
        )

        self.assertEqual(1, result["replaced"])
        rendered = str(request.contexts)
        self.assertIn("可见内容：一只橘猫", rendered)
        self.assertIn("小明", rendered)
        plugin._caption_context_image_sources.assert_not_awaited()

    async def test_explicit_original_image_request_forces_new_vision_call(self) -> None:
        plugin = _harness()
        request = SimpleNamespace(contexts=[{"role": "user", "content": "[图片]"}])

        result = await plugin._enrich_request_context_image_placeholders(
            SimpleNamespace(message_str="重新看一下原图"), request
        )

        self.assertEqual(1, result["replaced"])
        plugin._caption_context_image_sources.assert_awaited_once()
        self.assertTrue(plugin._caption_context_image_sources.await_args.kwargs["force_refresh"])

    async def test_normal_query_can_analyze_when_description_is_missing(self) -> None:
        plugin = _harness()
        plugin._context_image_cached_summary.return_value = ""
        request = SimpleNamespace(contexts=[{"role": "user", "content": "[图片]"}])

        result = await plugin._enrich_request_context_image_placeholders(
            SimpleNamespace(message_str="那张图是什么"), request
        )

        self.assertEqual(1, result["replaced"])
        plugin._caption_context_image_sources.assert_awaited_once()
        self.assertFalse(plugin._caption_context_image_sources.await_args.kwargs["force_refresh"])

    async def test_other_scope_image_is_not_recalled(self) -> None:
        plugin = _harness()
        plugin._event_scope_key = lambda _event: "group:other"
        request = SimpleNamespace(contexts=[{"role": "user", "content": "[图片]"}])

        result = await plugin._enrich_request_context_image_placeholders(
            SimpleNamespace(message_str="那张图呢"), request
        )

        self.assertEqual(0, result["replaced"])
        plugin._context_image_cached_summary.assert_not_awaited()
        plugin._caption_context_image_sources.assert_not_awaited()

    async def test_hub_denies_new_vision_but_preserves_cached_summary(self) -> None:
        plugin = _harness()
        request = SimpleNamespace(contexts=[{"role": "user", "content": "[图片]"}])
        event = SimpleNamespace(
            message_str="重新看一下原图",
            _private_companion_capability_decision=SimpleNamespace(actions=()),
        )

        result = await plugin._enrich_request_context_image_placeholders(event, request)

        self.assertEqual(1, result["replaced"])
        self.assertIn("一只橘猫", str(request.contexts))
        plugin._caption_context_image_sources.assert_not_awaited()

    async def test_group_observation_summary_is_used_when_snapshot_has_no_summary(self) -> None:
        plugin = _harness()
        plugin._context_image_cached_summary = PrivateCompanionPlugin._context_image_cached_summary.__get__(plugin)
        plugin._extract_group_id_from_event = lambda _event: "g1"
        plugin._get_group = lambda _group_id: {
            "recent_messages": [{"message_id": "m1", "image_vision": "图片类型：截图 可见内容：天气预报"}]
        }
        summary = await plugin._context_image_cached_summary(
            SimpleNamespace(),
            plugin._recall_message_cache["m1"],
        )

        self.assertIn("天气预报", summary)


if __name__ == "__main__":
    unittest.main()
