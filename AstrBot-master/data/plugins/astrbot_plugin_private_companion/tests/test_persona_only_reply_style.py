# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class PersonaOnlyReplyStyleTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_route_keeps_existing_review_local_without_gpt_rewriting(self):
        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enable_conversation_model_routing = True
        plugin._passive_response_review_enabled = lambda: True
        plugin._response_review_flags = lambda *args, **kwargs: ["leaks_internal"]
        plugin._response_denies_existing_creative_work = lambda *args: False
        plugin._unexecuted_relay_claim_reason = lambda *args: ""
        plugin._effective_passive_review_mode = lambda: "full"
        plugin._effective_passive_review_strength = lambda: "normal"
        plugin._fallback_temporal_or_continuity_confused_reply = lambda *args, **kwargs: "本地纠正"
        plugin._llm_call = AsyncMock(side_effect=AssertionError("Remote review must not rewrite the chat reply"))
        event = SimpleNamespace(_private_companion_conversation_route="chat")
        result = await plugin._review_and_rewrite_response({}, "你好", "原回复", review_event=event)
        self.assertEqual("本地纠正", result)
        plugin._llm_call.assert_not_awaited()

    async def test_private_and_group_final_requests_skip_plugin_style_but_keep_hard_accuracy_guard(self):
        for mode in ("private", "group"):
            with self.subTest(mode=mode):
                plugin = object.__new__(PrivateCompanionPlugin)
                plugin._format_reply_style_prompt = lambda: "插件风格：必须简短克制"
                plugin._format_technical_reasoning_prompt = lambda _event, _req: "技术事实必须准确"
                plugin._append_turn_prompt_fragment_by_position = lambda *_args, **_kwargs: False

                async def record_fragment(*_args, **_kwargs):
                    return None

                plugin._record_request_prompt_fragment = record_fragment
                event = SimpleNamespace(message_str="日志里的耗时是多少秒")
                request = SimpleNamespace(
                    system_prompt="当前会话已选人格：傲娇损友",
                    prompt="",
                )

                await plugin._append_reply_style_to_request(event, request, mode=mode)

                self.assertIn("当前会话已选人格：傲娇损友", request.system_prompt)
                self.assertNotIn("插件风格：必须简短克制", request.system_prompt)
                self.assertIn("技术事实必须准确", request.system_prompt)


if __name__ == "__main__":
    unittest.main()
