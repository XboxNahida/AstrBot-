# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from copy import deepcopy
from types import SimpleNamespace

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class ToolCallContextIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        self.event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")

    @staticmethod
    def _assistant(*call_ids: str) -> dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
                for call_id in call_ids
            ],
        }

    @staticmethod
    def _tool(call_id: str, content: str = "ok") -> dict:
        return {"role": "tool", "tool_call_id": call_id, "content": content}

    def test_incomplete_parallel_tool_group_is_removed_atomically(self) -> None:
        contexts = [
            {"role": "user", "content": "查两项"},
            self._assistant("call_a", "call_b"),
            self._tool("call_a"),
            {"role": "assistant", "content": "继续"},
        ]
        req = SimpleNamespace(contexts=contexts)

        self.plugin._repair_incomplete_tool_context_groups(self.event, req)

        self.assertEqual(
            req.contexts,
            [
                {"role": "user", "content": "查两项"},
                {"role": "assistant", "content": "继续"},
            ],
        )

    def test_complete_tool_group_is_preserved_and_duplicate_result_is_removed(self) -> None:
        assistant = self._assistant("call_a", "call_b")
        first = self._tool("call_a", "one")
        second = self._tool("call_b", "two")
        req = SimpleNamespace(
            contexts=[assistant, first, self._tool("call_a", "duplicate"), second]
        )

        self.plugin._repair_incomplete_tool_context_groups(self.event, req)

        self.assertEqual(req.contexts, [assistant, first, second])

    def test_orphan_tool_message_is_removed(self) -> None:
        req = SimpleNamespace(
            contexts=[
                {"role": "user", "content": "你好"},
                self._tool("missing"),
                {"role": "assistant", "content": "你好"},
            ]
        )

        self.plugin._repair_incomplete_tool_context_groups(self.event, req)

        self.assertEqual([item["role"] for item in req.contexts], ["user", "assistant"])

    def test_deepseek_tool_guard_requests_sequential_calls_without_disabling_tools(self) -> None:
        self.plugin._llm_request_provider_identity_parts = lambda _event, _req: [
            "deepseek/deepseek-v4-pro"
        ]
        tool_set = object()
        req = SimpleNamespace(func_tool=tool_set, system_prompt="原提示")

        changed = self.plugin._append_deepseek_tool_protocol_guard(self.event, req)

        self.assertTrue(changed)
        self.assertIs(req.func_tool, tool_set)
        self.assertIn("按顺序逐个调用", req.system_prompt)
        self.assertIn("每条 assistant 消息只发起一个工具调用", req.system_prompt)
        self.assertIn("普通文字时，直接输出最终回复", req.system_prompt)
        self.assertIn("plain 文本不得为空", req.system_prompt)

    def test_tool_guard_does_not_change_non_deepseek_provider(self) -> None:
        self.plugin._llm_request_provider_identity_parts = lambda _event, _req: [
            "openai/gpt-5.4"
        ]
        req = SimpleNamespace(func_tool=object(), system_prompt="原提示")

        changed = self.plugin._append_deepseek_tool_protocol_guard(self.event, req)

        self.assertFalse(changed)
        self.assertEqual(req.system_prompt, "原提示")

    def test_long_group_history_uses_same_guard_as_private_history(self) -> None:
        contexts = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"成员甲：消息{i}"}
            for i in range(174)
        ]
        original = deepcopy(contexts)
        private = SimpleNamespace(contexts=contexts, prompt="本轮消息")
        group = SimpleNamespace(contexts=contexts, prompt="本轮消息")
        self.plugin._trim_passive_request_context_if_needed(
            self.event, private, is_private_chat=True
        )
        self.plugin._trim_passive_request_context_if_needed(
            SimpleNamespace(unified_msg_origin="default:GroupMessage:20001"),
            group,
            is_private_chat=False,
        )
        self.assertEqual(len(group.contexts), 24)
        self.assertEqual(group.contexts, private.contexts)
        self.assertEqual(group.contexts[-1]["content"], "成员甲：消息173")
        self.assertEqual(contexts, original)
        self.assertEqual(group.prompt, "本轮消息")

    def test_group_guard_removes_old_tool_protocol_and_media_payloads(self) -> None:
        contexts = [{"role": "user", "content": "旧消息"} for _ in range(120)]
        contexts.extend([
            self._assistant("old_call"),
            self._tool("old_call", "旧工具结果"),
            {"role": "assistant", "content": "历史答复", "tool_calls": []},
            {"role": "user", "content": [
                {"type": "text", "text": "成员乙：看看这个"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,old"}},
            ]},
        ])
        original = deepcopy(contexts)
        req = SimpleNamespace(contexts=contexts)
        self.plugin._trim_passive_request_context_if_needed(
            self.event, req, is_private_chat=False
        )
        self.assertLessEqual(len(req.contexts), 24)
        self.assertTrue(all(set(item) == {"role", "content"} for item in req.contexts))
        self.assertTrue(all(item["role"] != "tool" for item in req.contexts))
        self.assertEqual(req.contexts[-1]["content"], "成员乙：看看这个 [图片]")
        self.assertEqual(contexts, original)

    def test_large_group_text_uses_private_character_threshold(self) -> None:
        req = SimpleNamespace(contexts=[
            {"role": "user", "content": "长" * 8000} for _ in range(30)
        ])
        self.plugin._trim_passive_request_context_if_needed(
            self.event, req, is_private_chat=False
        )
        self.assertEqual(len(req.contexts), 24)
        self.assertTrue(all(len(item["content"]) <= 1200 for item in req.contexts))

    def test_short_group_and_private_history_remain_unchanged(self) -> None:
        for private in (False, True):
            with self.subTest(private=private):
                contexts = [{"role": "user", "content": "成员丙：你好"}]
                req = SimpleNamespace(contexts=contexts)
                self.plugin._trim_passive_request_context_if_needed(
                    self.event, req, is_private_chat=private
                )
                self.assertIs(req.contexts, contexts)


if __name__ == "__main__":
    unittest.main()
