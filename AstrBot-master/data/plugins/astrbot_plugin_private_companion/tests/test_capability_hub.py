# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.capability_hub import (
    build_capability_context,
    capability_allowed,
    decision_action_allowed,
    decide_capabilities,
    format_capability_guidance,
    judge_capabilities,
    parse_model_capability_decision,
)
from astrbot_plugin_private_companion.passive_state_pipeline import (
    append_capability_guidance,
    gate_request_search_tools,
)


class CapabilityHubTests(unittest.TestCase):
    def _context(self, text: str, *, recent=(), scope="group:g1"):
        return build_capability_context(
            scope=scope,
            current_text=text,
            sender_id="u1",
            sender_name="小明",
            now=datetime(2026, 9, 28, 12, tzinfo=timezone.utc),
            previous_message_at=datetime(2026, 9, 28, 11, 59, 50, tzinfo=timezone.utc),
            recent_messages=recent,
        )

    def test_decision_uses_one_deduplicated_action_list(self) -> None:
        decision = decide_capabilities(
            self._context("帮我看看这张图，顺便查一下今天的天气"),
            available_capabilities={"vision", "search", "emoji", "voice"},
        )

        self.assertTrue(decision.reply)
        self.assertEqual(("vision", "search"), decision.actions)
        self.assertNotIn("voice", decision.actions)
        self.assertFalse(decision.merge_messages)

    def test_model_decision_parses_actions_tone_and_burst(self) -> None:
        decision = parse_model_capability_decision(
            '{"reply":true,"actions":["emoji","unknown"],'
            '"tone":"轻松调侃","emotion":"开心",'
            '"merge_messages":true,"reason":"同话题连续消息"}',
            available_capabilities={"emoji", "vision"},
            context=self._context("大家都在聊这个梗"),
        )

        self.assertIsNotNone(decision)
        self.assertTrue(decision.reply)
        self.assertEqual(("emoji",), decision.actions)
        self.assertEqual("轻松调侃", decision.reply_style)
        self.assertEqual("开心", decision.emotion)
        self.assertTrue(decision.merge_messages)

    def test_model_decision_bounds_reply_candidate_and_clears_it_when_not_replying(self) -> None:
        candidate = "接住群友刚才的问题"
        decision = parse_model_capability_decision(
            json.dumps({"reply": True, "actions": [], "reply_candidate": candidate}, ensure_ascii=False),
            available_capabilities=(),
            context=self._context("这个怎么用？"),
        )
        self.assertIsNotNone(decision)
        self.assertEqual(candidate, decision.reply_candidate)

        long_decision = parse_model_capability_decision(
            json.dumps({"reply": True, "actions": [], "reply_candidate": "甲" * 300}, ensure_ascii=False),
            available_capabilities=(),
            context=self._context("这个怎么用？"),
        )
        self.assertIsNotNone(long_decision)
        self.assertLessEqual(len(long_decision.reply_candidate), 240)

        non_string_decision = parse_model_capability_decision(
            '{"reply":true,"actions":[],"reply_candidate":{"text":"不应保留"}}',
            available_capabilities=(),
            context=self._context("这个怎么用？"),
        )
        self.assertIsNotNone(non_string_decision)
        self.assertEqual("", non_string_decision.reply_candidate)

        silent_decision = parse_model_capability_decision(
            '{"reply":false,"actions":[],"reply_candidate":"不应发送"}',
            available_capabilities=(),
            context=self._context("这个怎么用？"),
        )
        self.assertIsNotNone(silent_decision)
        self.assertEqual("", silent_decision.reply_candidate)

    def test_model_decision_rejects_image_edit_without_explicit_model_intent(self) -> None:
        decision = parse_model_capability_decision(
            '{"reply":true,"actions":["image_edit"],'
            '"image_edit_requested":false,"tone":"自然"}',
            available_capabilities={"image_edit"},
            context=self._context("这张图怎么样"),
        )

        self.assertIsNotNone(decision)
        self.assertNotIn("image_edit", decision.actions)

    def test_model_decision_bad_json_returns_none(self) -> None:
        self.assertIsNone(
            parse_model_capability_decision(
                "不是 JSON",
                available_capabilities={"emoji"},
                context=self._context("你好"),
            )
        )

    def test_request_scoped_decision_is_authoritative_for_external_actions(self) -> None:
        decision = decide_capabilities(
            self._context("请发一个表情包"),
            available_capabilities=("emoji", "image_edit"),
        )
        self.assertEqual(decision.actions, ("emoji",))
        self.assertTrue(decision_action_allowed(decision, "emoji"))
        self.assertFalse(decision_action_allowed(decision, "image_edit"))
        self.assertTrue(decision_action_allowed(None, "image_edit"))

    def test_decision_only_edits_when_user_requests_image_change(self) -> None:
        decision = decide_capabilities(
            self._context("这张图挺好看的"),
            available_capabilities={"vision", "image_edit"},
        )
        self.assertNotIn("image_edit", decision.actions)

        edit = decide_capabilities(
            self._context("把这张图的背景改成夜景"),
            available_capabilities={"vision", "image_edit"},
        )
        self.assertIn("image_edit", edit.actions)

    def test_visual_followup_and_original_refresh_select_vision(self) -> None:
        for text in ("这张图是什么", "重新看一下原图", "再仔细看一下刚才的照片"):
            with self.subTest(text=text):
                decision = decide_capabilities(
                    self._context(text), available_capabilities={"vision"}
                )
                self.assertIn("vision", decision.actions)

    def test_current_image_without_text_selects_vision(self) -> None:
        decision = decide_capabilities(
            self._context(""),
            available_capabilities={"vision"},
            has_current_image=True,
        )
        self.assertTrue(decision.reply)
        self.assertEqual(("vision",), decision.actions)

    def test_inbound_voice_is_context_only_not_outbound_voice_request(self) -> None:
        context = build_capability_context(
            scope="private:u1",
            current_text="",
            sender_id="u1",
            sender_name="小明",
            now=datetime(2026, 9, 28, 12, tzinfo=timezone.utc),
            has_current_voice=True,
        )
        decision = decide_capabilities(
            context,
            available_capabilities={"voice", "vision"},
        )
        self.assertTrue(decision.reply)
        self.assertEqual((), decision.actions)
        self.assertTrue(context.has_current_voice)

    def test_decision_merges_cross_sender_burst_only_after_pending_reply(self) -> None:
        decision = decide_capabilities(
            self._context("那就继续说", recent=(
                {"scope": "group:g1", "sender_id": "u2", "text": "小七怎么看"},
                {"scope": "group:g1", "sender_id": "u3", "text": "我也想知道"},
            )),
            available_capabilities=(),
            pending_reply=True,
            burst_count=3,
        )
        self.assertTrue(decision.merge_messages)
        self.assertTrue(decision.reply)

    def test_decision_does_not_merge_unrelated_message_or_repeat_old_actions(self) -> None:
        decision = decide_capabilities(
            self._context("群友们，明天几点集合", recent=(
                {"scope": "group:g1", "sender_id": "u2", "text": "小七看下这张图"},
            )),
            available_capabilities={"vision", "search"},
            pending_reply=True,
            burst_count=4,
            topic_related=False,
            previous_actions=("vision",),
        )
        self.assertFalse(decision.merge_messages)
        self.assertNotIn("vision", decision.actions)
        self.assertTrue(decision.reply)

    def test_context_keeps_only_same_scope_recent_messages(self) -> None:
        now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        context = build_capability_context(
            scope="group:g1",
            current_text="刚才那张图呢",
            sender_id="u1",
            sender_name="小明",
            now=now,
            recent_messages=(
                {"scope": "group:g1", "text": "这是照片", "sender_name": "小明"},
                {"scope": "group:g2", "text": "不要混入其他群", "sender_name": "小红"},
            ),
        )

        self.assertEqual("group", context.conversation_type)
        self.assertEqual(1, len(context.recent_messages))
        self.assertEqual("这是照片", context.recent_messages[0].text)

    def test_context_calculates_nonnegative_gap_from_aware_times(self) -> None:
        now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        context = build_capability_context(
            scope="private:u1",
            current_text="你好",
            sender_id="u1",
            sender_name="小明",
            now=now,
            previous_message_at=now - timedelta(seconds=75),
        )
        future_context = build_capability_context(
            scope="private:u1",
            current_text="你好",
            sender_id="u1",
            sender_name="小明",
            now=now,
            previous_message_at=now + timedelta(seconds=5),
        )

        self.assertEqual(75.0, context.last_message_gap_seconds)
        self.assertEqual(0.0, future_context.last_message_gap_seconds)

    def test_naive_or_missing_timestamps_remain_unknown(self) -> None:
        context = build_capability_context(
            scope="private:u1",
            current_text="你好",
            sender_id="u1",
            sender_name="小明",
            now=datetime(2026, 9, 28, 12),
            previous_message_at="not-a-timestamp",
        )

        self.assertIsNone(context.now)
        self.assertIsNone(context.last_message_gap_seconds)

    def test_guidance_includes_time_gap_speaker_and_image_rules(self) -> None:
        now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        context = build_capability_context(
            scope="group:g1",
            current_text="把背景换成夜景",
            sender_id="u1",
            sender_name="小明",
            now=now,
            previous_message_at=now - timedelta(seconds=30),
            recent_messages=(
                {"scope": "group:g1", "text": "[图片]", "image_summary": "海边照片"},
            ),
            available_capabilities=("image_read", "image_edit", "voice_send"),
        )

        guidance = format_capability_guidance(context)
        self.assertIn("小明", guidance)
        self.assertIn("30 秒", guidance)
        self.assertIn("海边照片", guidance)
        self.assertIn("仅当用户确实在请求修改图片", guidance)
        self.assertIn("群聊或私聊", guidance)
        self.assertIn("不可信对话数据", guidance)

    def test_image_edit_requires_contextual_user_intent_not_topic_mention(self) -> None:
        self.assertFalse(
            capability_allowed(
                "image_edit", confidence=0.99, explicit_user_intent=False
            )
        )
        self.assertTrue(
            capability_allowed("image_edit", confidence=0.7, explicit_user_intent=True)
        )
        self.assertFalse(
            capability_allowed(
                "image_edit",
                confidence=0.99,
                explicit_user_intent=True,
                platform_supported=False,
            )
        )

    def test_proactive_voice_is_allowed_in_private_and_group(self) -> None:
        for conversation_type in ("private", "group"):
            with self.subTest(conversation_type=conversation_type):
                self.assertTrue(
                    capability_allowed(
                        "voice_send",
                        confidence=0.8,
                        explicit_user_intent=False,
                        conversation_type=conversation_type,
                    )
                )

    def test_quote_and_mention_require_reliable_target_and_purpose(self) -> None:
        self.assertFalse(
            capability_allowed(
                "quote_reply",
                confidence=0.9,
                explicit_user_intent=True,
                target_available=False,
            )
        )
        self.assertFalse(
            capability_allowed(
                "mention",
                confidence=0.9,
                explicit_user_intent=False,
                target_available=True,
            )
        )
        self.assertTrue(
            capability_allowed(
                "mention",
                confidence=0.8,
                explicit_user_intent=True,
                target_available=True,
            )
        )


class CapabilityGuidanceInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_uses_independent_provider_and_persona_emotion_context(self):
        llm_call = AsyncMock(return_value=json.dumps({
            "reply": True, "actions": [], "route": "mixed",
            "emotion_event": {"kind": "tease", "intensity": 0.7, "confidence": 0.9,
                              "targets_bot": True, "phase": "covering"},
        }))
        plugin = SimpleNamespace(
            llm_provider_id="legacy-gpt", conversation_hub_provider_id="hub-fixed",
            enable_short_term_emotion=True, enable_conversation_model_routing=True,
            _task_provider=lambda *ids: next((item for item in ids if item), ""),
            _llm_call=llm_call,
        )
        context = build_capability_context(
            scope="private:u1", current_text="你就是关心我，顺便帮我算一下",
            sender_id="u1", sender_name="甲", now=datetime.now(timezone.utc),
        )
        decision = await judge_capabilities(
            plugin, context, available_capabilities=(), persona_prompt="嘴硬但关心对方",
            short_term_state={"embarrassment": 0.3},
        )
        self.assertEqual("hub-fixed", llm_call.await_args.kwargs["provider_id"])
        self.assertEqual("mixed", decision.route)
        self.assertEqual("tease", decision.emotion_event["kind"])
        self.assertIn("嘴硬但关心对方", llm_call.await_args.args[0])
        self.assertIn("embarrassment", llm_call.await_args.args[0])

    def test_route_and_emotion_cannot_inject_provider_or_unbounded_values(self):
        context = build_capability_context(
            scope="private:u1", current_text="你好", sender_id="u1", sender_name="甲",
            now=datetime.now(timezone.utc),
        )
        decision = parse_model_capability_decision(
            json.dumps({"reply": True, "actions": [], "route": "openai/gpt",
                        "emotion_event": {"kind": "invented", "intensity": 999}}),
            available_capabilities=(), context=context,
        )
        self.assertEqual("chat", decision.route)
        self.assertEqual({}, decision.emotion_event)

    def test_task_estimate_and_mixed_segments_are_bounded(self):
        context = build_capability_context(
            scope="private:u1", current_text="先说明一下，再帮我分析项目",
            sender_id="u1", sender_name="甲", now=datetime.now(timezone.utc),
        )
        decision = parse_model_capability_decision(
            json.dumps({
                "reply": True, "actions": [], "route": "mixed",
                "estimated_seconds": 99999,
                "segments": [
                    {"kind": "chat", "text": "先说明一下"},
                    {"kind": "task", "text": "帮我分析项目"},
                    {"kind": "evil", "text": "丢弃"},
                ],
            }),
            available_capabilities=(), context=context,
        )
        self.assertEqual(3600, decision.estimated_seconds)
        self.assertEqual(("chat", "先说明一下"), decision.segments[0])
        self.assertEqual(("task", "帮我分析项目"), decision.segments[1])
        self.assertEqual(2, len(decision.segments))

    async def test_model_judge_uses_reply_provider_and_standard_fallback_route(self) -> None:
        llm_call = AsyncMock(
            return_value='{"reply":true,"actions":[],"tone":"自然"}'
        )
        plugin = SimpleNamespace(
            llm_provider_id="reply-primary",
            _task_provider=lambda *provider_ids: provider_ids[0] or "",
            _llm_call=llm_call,
        )
        context = build_capability_context(
            scope="private:u1",
            current_text="你好",
            sender_id="u1",
            sender_name="小明",
            now=datetime(2026, 9, 29, tzinfo=timezone.utc),
        )

        decision = await judge_capabilities(
            plugin,
            context,
            available_capabilities=("emoji",),
        )

        self.assertTrue(decision.reply)
        self.assertEqual("reply-primary", llm_call.await_args.kwargs["provider_id"])
        self.assertEqual("astrbot_reply", llm_call.await_args.kwargs["task"])

    async def test_explicit_group_reaction_request_is_not_rejected_as_group_chat(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {"recent_messages": []},
            _recall_message_cache={},
            _central_hub_scheduler_enabled=True,
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(return_value='{"reply":true,"actions":["emoji"],"tone":"轻松"}'),
        )
        event = SimpleNamespace(
            message_str="发一个表情包",
            message_obj=SimpleNamespace(message_id="m-explicit-emoji"),
            private_companion_group_scene={
                "talking_to": "group",
                "trigger": "group_message",
            },
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )

        await append_capability_guidance(plugin, event, None)

        self.assertTrue(event._private_companion_capability_decision.reply)
        self.assertIn("emoji", event._private_companion_capability_decision.actions)

    async def test_group_reply_qualification_is_owned_by_hub(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {"recent_messages": []},
            _recall_message_cache={},
            _central_hub_scheduler_enabled=True,
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(side_effect=[
                '{"reply":false,"actions":[],"tone":"none"}',
                '{"reply":true,"actions":[],"tone":"自然"}',
            ]),
        )
        event = SimpleNamespace(
            message_str="这句话是在群里聊天",
            message_obj=SimpleNamespace(message_id="m1"),
            private_companion_group_scene={
                "talking_to": "group",
                "trigger": "group_message",
            },
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )

        await append_capability_guidance(plugin, event, None)

        self.assertFalse(event._private_companion_capability_decision.reply)

        event = SimpleNamespace(
            message_str="这句话是在继续和小七说",
            message_obj=SimpleNamespace(message_id="m2"),
            private_companion_group_scene={
                "talking_to": "bot",
                "trigger": "group_message",
            },
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        await append_capability_guidance(plugin, event, None)

        self.assertTrue(event._private_companion_capability_decision.reply)

    async def test_group_hub_reply_upgrades_non_wakeup_event(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {"recent_messages": []},
            _recall_message_cache={},
            _central_hub_scheduler_enabled=True,
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(return_value='{"reply":true,"actions":[],"tone":"自然"}'),
        )
        event = SimpleNamespace(
            message_str="你现在是什么模型",
            message_obj=SimpleNamespace(message_id="m-upgrade"),
            private_companion_group_scene={
                "talking_to": "group",
                "trigger": "group_message",
            },
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
            is_wake=False,
            is_at_or_wake_command=False,
        )

        await append_capability_guidance(plugin, event, None)

        self.assertTrue(event._private_companion_capability_decision.reply)
        self.assertTrue(event.is_wake)
        self.assertTrue(event.is_at_or_wake_command)

    async def test_group_hub_silence_does_not_upgrade_non_wakeup_event(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {"recent_messages": []},
            _recall_message_cache={},
            _central_hub_scheduler_enabled=True,
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(return_value='{"reply":false,"actions":[],"tone":"none"}'),
        )
        event = SimpleNamespace(
            message_str="普通闲聊",
            message_obj=SimpleNamespace(message_id="m-silent"),
            private_companion_group_scene={
                "talking_to": "group",
                "trigger": "group_message",
            },
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
            is_wake=False,
            is_at_or_wake_command=False,
        )

        await append_capability_guidance(plugin, event, None)

        self.assertFalse(event._private_companion_capability_decision.reply)
        self.assertFalse(event.is_wake)
        self.assertFalse(event.is_at_or_wake_command)

    async def test_related_group_topic_can_continue_without_explicit_wakeup(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {
                "recent_messages": [
                    {
                        "message_id": "m0",
                        "ts": datetime.now(timezone.utc).timestamp() - 280,
                        "sender_id": "u1",
                        "name": "小明",
                        "text": "小七，评价一下贴吧鱼",
                        "talking_to": "bot",
                        "scene_trigger": "group_wakeup_direct_word",
                    },
                    {
                        "message_id": "b0",
                        "ts": datetime.now(timezone.utc).timestamp() - 270,
                        "sender_id": "bot",
                        "name": "小七",
                        "text": "贴吧鱼这个说法需要结合上下文判断",
                        "talking_to": "group",
                        "scene_trigger": "bot_reply",
                    },
                ]
            },
            _recall_message_cache={},
            _central_hub_scheduler_enabled=True,
        )
        event = SimpleNamespace(
            message_str="贴吧鱼是AI",
            message_obj=SimpleNamespace(message_id="m1"),
            private_companion_group_scene={
                "talking_to": "group",
                "trigger": "group_message",
            },
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )

        await append_capability_guidance(plugin, event, None)

        self.assertTrue(event._private_companion_capability_decision.reply)

    async def test_related_group_topic_uses_structured_bot_target_in_history(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {
                "recent_messages": [
                    {
                        "message_id": "m0",
                        "ts": datetime.now(timezone.utc).timestamp() - 20,
                        "sender_id": "u1",
                        "name": "小明",
                        "text": "只准自己看哈",
                        "talking_to": "bot",
                        "scene_trigger": "reply_bot",
                    }
                ]
            },
            _recall_message_cache={},
            _central_hub_scheduler_enabled=True,
        )
        event = SimpleNamespace(
            message_str="自己看什么",
            message_obj=SimpleNamespace(message_id="m1"),
            private_companion_group_scene={
                "talking_to": "group",
                "trigger": "group_message",
            },
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )

        await append_capability_guidance(plugin, event, None)

        self.assertTrue(event._private_companion_capability_decision.reply)

    def test_short_followup_inherits_recent_explicit_emoji_request(self) -> None:
        now = datetime.now(timezone.utc)
        context = build_capability_context(
            scope="private:u1",
            current_text="现在呢",
            sender_id="u1",
            sender_name="小明",
            now=now,
            recent_messages=[
                {
                    "scope": "private:u1",
                    "sender_id": "u1",
                    "text": "现在表情包了吗",
                    "timestamp": now,
                }
            ],
            available_capabilities=("emoji",),
        )

        decision = decide_capabilities(
            context,
            available_capabilities=("emoji",),
        )

        self.assertIn("emoji", decision.actions)

    def test_repeat_image_request_inherits_emoji_without_repeating_keyword(self) -> None:
        now = datetime(2026, 9, 29, 10, tzinfo=timezone.utc)
        context = build_capability_context(
            scope="group:g1",
            current_text="再来一张",
            sender_id="u1",
            sender_name="小明",
            now=now,
            recent_messages=[
                {
                    "scope": "group:g1",
                    "sender_id": "u1",
                    "text": "发一个表情包",
                    "timestamp": now - timedelta(seconds=20),
                }
            ],
            available_capabilities=("emoji",),
        )

        decision = decide_capabilities(context, available_capabilities=("emoji",))

        self.assertIn("emoji", decision.actions)

    def test_group_followup_does_not_inherit_another_members_tool_request(self) -> None:
        now = datetime(2026, 9, 29, 10, tzinfo=timezone.utc)
        context = build_capability_context(
            scope="group:g1",
            current_text="再来一张",
            sender_id="u1",
            sender_name="小明",
            now=now,
            recent_messages=[
                {
                    "scope": "group:g1",
                    "sender_id": "u2",
                    "text": "发一个表情包",
                    "timestamp": now - timedelta(seconds=20),
                }
            ],
            available_capabilities=("emoji",),
        )

        decision = decide_capabilities(context, available_capabilities=("emoji",))

        self.assertNotIn("emoji", decision.actions)

    def test_short_followup_inherits_each_recent_tool_request(self) -> None:
        cases = (
            ("刚才说的图片现在呢", "看看这张图片", "vision"),
            ("结果呢", "查一下最新天气", "search"),
            ("现在可以了吗", "把这张图改成黑白", "image_edit"),
            ("发了吗", "请发语音朗读", "voice"),
            ("现在呢", "现在表情包了吗", "emoji"),
        )
        for current, previous, expected in cases:
            now = datetime.now(timezone.utc)
            context = build_capability_context(
                scope="private:u1",
                current_text=current,
                sender_id="u1",
                sender_name="小明",
                now=now,
                recent_messages=[
                    {
                        "scope": "private:u1",
                        "sender_id": "u1",
                        "text": previous,
                        "timestamp": now,
                    }
                ],
                available_capabilities=("vision", "search", "image_edit", "voice", "emoji"),
            )
            decision = decide_capabilities(
                context,
                available_capabilities=context.available_capabilities,
            )
            self.assertIn(expected, decision.actions, (current, previous))

    async def test_group_explicit_wakeup_bypasses_reply_qualification(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {"recent_messages": []},
            _recall_message_cache={},
            _central_hub_scheduler_enabled=True,
        )
        for trigger in ("at_bot", "reply_bot", "group_wakeup_name"):
            event = SimpleNamespace(
                message_str="小七，回答一下",
                message_obj=SimpleNamespace(message_id=f"m-{trigger}"),
                private_companion_group_scene={
                    "talking_to": "group",
                    "trigger": trigger,
                },
                is_private_chat=lambda: False,
                get_sender_id=lambda: "u1",
                get_sender_name=lambda: "小明",
            )

            await append_capability_guidance(plugin, event, None)

            self.assertTrue(
                event._private_companion_capability_decision.reply,
                trigger,
            )

    async def test_image_preflight_decision_is_reused_by_request(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "private:u1",
            _recall_message_cache={},
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(return_value='{"reply":true,"actions":["vision"],"tone":"自然"}'),
        )
        event = SimpleNamespace(
            message_str="看看今天拍的照片",
            message_obj=SimpleNamespace(message_id="m1"),
            private_companion_delayed_image_sources=["saved-image"],
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        await append_capability_guidance(plugin, event, None)
        preflight = event._private_companion_capability_decision
        self.assertIn("vision", preflight.actions)

        request = SimpleNamespace(
            system_prompt="原有提示",
            func_tool=SimpleNamespace(tools=[SimpleNamespace(name="web_search_tavily")]),
        )
        await append_capability_guidance(plugin, event, request)

        self.assertIs(preflight, request._private_companion_capability_decision)
        self.assertIn("vision", request._private_companion_capability_decision.actions)
        self.assertNotIn("search", request._private_companion_capability_decision.actions)
        plugin._llm_call.assert_awaited_once()

    async def test_reply_guidance_uses_candidate_without_tone_or_emotion_commands(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "private:u1",
            _recall_message_cache={},
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(
                return_value=(
                    '{"reply":true,"actions":["emoji"],"tone":"活泼",'
                    '"emotion":"开心","reply_candidate":"接住刚才的玩笑"}'
                )
            ),
        )
        event = SimpleNamespace(
            message_str="哈哈这个梗好笑",
            message_obj=SimpleNamespace(message_id="candidate-guidance"),
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        await append_capability_guidance(plugin, event, None)
        request = SimpleNamespace(system_prompt="当前人格提示", func_tool=None)

        await append_capability_guidance(plugin, event, request)

        self.assertIn("接住刚才的玩笑", request.system_prompt)
        self.assertIn("不可直接发送", request.system_prompt)
        self.assertNotIn("语气=活泼", request.system_prompt)
        self.assertNotIn("情绪线索=开心", request.system_prompt)
        self.assertIn("当前人格提示", request.system_prompt)
        self.assertEqual("", request.prompt if hasattr(request, "prompt") else "")
        self.assertEqual(("emoji",), request._private_companion_capability_decision.actions)
        plugin._llm_call.assert_awaited_once()

    async def test_preflight_search_is_removed_when_request_has_no_tool(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "private:u1",
            _recall_message_cache={},
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(return_value='{"reply":true,"actions":["vision"],"tone":"自然"}'),
        )
        event = SimpleNamespace(
            message_str="看看今天拍的照片",
            message_obj=SimpleNamespace(message_id="m1"),
            private_companion_delayed_image_sources=["saved-image"],
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        await append_capability_guidance(plugin, event, None)
        request = SimpleNamespace(system_prompt="原有提示", func_tool=None)

        await append_capability_guidance(plugin, event, request)

        self.assertIn("vision", request._private_companion_capability_decision.actions)
        self.assertNotIn("search", request._private_companion_capability_decision.actions)

    async def test_search_tools_follow_hub_decision(self) -> None:
        class Tools:
            def __init__(self) -> None:
                self.tools = [SimpleNamespace(name=name) for name in ("web_search_tavily", "AIsearch", "safe_tool")]

            def remove_tool(self, name: str) -> None:
                self.tools = [tool for tool in self.tools if tool.name != name]

        request = SimpleNamespace(func_tool=Tools())
        request._private_companion_capability_decision = SimpleNamespace(actions=())
        gate_request_search_tools(request)
        self.assertEqual(["safe_tool"], [tool.name for tool in request.func_tool.tools])

        approved = SimpleNamespace(func_tool=Tools())
        approved._private_companion_capability_decision = SimpleNamespace(actions=("search",))
        gate_request_search_tools(approved)
        self.assertEqual(3, len(approved.func_tool.tools))

    async def test_injects_same_group_recent_messages_and_image_summary(self) -> None:
        previous_ts = datetime.now(timezone.utc).timestamp() - 30
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {
                "recent_messages": [
                    {
                        "message_id": "m1",
                        "ts": previous_ts,
                        "sender_id": "u1",
                        "name": "小明",
                        "text": "看这张图片",
                        "image_vision": "一只橘猫在窗台上",
                    },
                    {
                        "message_id": "m2",
                        "ts": datetime.now(timezone.utc).timestamp(),
                        "sender_id": "u1",
                        "name": "小明",
                        "text": "这是什么品种",
                    },
                ]
            },
            _recall_message_cache={},
        )
        event = SimpleNamespace(
            message_str="这是什么品种",
            message_obj=SimpleNamespace(message_id="m2"),
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        request = SimpleNamespace(system_prompt="原有系统提示")

        await append_capability_guidance(plugin, event, request)

        self.assertIn("原有系统提示", request.system_prompt)
        self.assertIn("一只橘猫在窗台上", request.system_prompt)
        self.assertIn("距前一条消息：", request.system_prompt)
        self.assertIn("群聊", request.system_prompt)
        decision = request._private_companion_capability_decision
        self.assertIs(event._private_companion_capability_decision, decision)
        self.assertEqual(decision.actions, ())

    async def test_request_gets_one_deduplicated_capability_decision(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "private:u1",
            _extract_group_id_from_event=lambda _event: "",
            _get_group=lambda _group_id: {},
            _recall_message_cache={},
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(return_value='{"reply":true,"actions":["vision","search"],"tone":"自然"}'),
        )
        event = SimpleNamespace(
            message_str="帮我看看这张图，顺便查一下今天的天气",
            message_obj=SimpleNamespace(message_id="m1"),
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        request = SimpleNamespace(
            system_prompt="原有提示",
            func_tool=SimpleNamespace(tools=[SimpleNamespace(name="web_search_tavily")]),
        )

        await append_capability_guidance(plugin, event, request)

        decision = request._private_companion_capability_decision
        self.assertIs(event._private_companion_capability_decision, decision)
        self.assertEqual(decision.actions, ("vision", "search"))

        self.assertIn("vision、search", request.system_prompt)

    async def test_force_rejudge_uses_merged_message_stream(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "group:g1",
            _extract_group_id_from_event=lambda _event: "g1",
            _get_group=lambda _group_id: {
                "recent_messages": [
                    {"sender_id": "u1", "sender_name": "甲", "text": "算法复杂度"},
                    {"sender_id": "u2", "sender_name": "乙", "text": "空间复杂度呢"},
                ]
            },
            _recall_message_cache={},
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(
                side_effect=[
                    '{"reply":true,"actions":[],"tone":"自然","merge_messages":true}',
                    '{"reply":false,"actions":[],"tone":"none","merge_messages":false}',
                ]
            ),
        )
        event = SimpleNamespace(
            message_str="空间复杂度呢",
            private_companion_group_text="算法复杂度\n空间复杂度呢",
            message_obj=SimpleNamespace(message_id="m2"),
            private_companion_group_scene={"talking_to": "group"},
            is_private_chat=lambda: False,
            get_sender_id=lambda: "u2",
            get_sender_name=lambda: "乙",
        )
        request = SimpleNamespace(system_prompt="原有提示")

        await append_capability_guidance(plugin, event, None)
        first = event._private_companion_capability_decision
        await append_capability_guidance(plugin, event, request, force_rejudge=True)

        self.assertTrue(first.merge_messages)
        self.assertFalse(request._private_companion_capability_decision.reply)
        self.assertEqual(2, plugin._llm_call.await_count)

    async def test_existing_hub_marker_still_refreshes_decision_from_history(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "private:u1",
            _task_provider=lambda *args: "reply-primary",
            _llm_call=AsyncMock(return_value='{"reply":true,"actions":["emoji"],"tone":"自然"}'),
            _recall_message_cache={
                "m0": {
                    "message_id": "m0",
                    "scope": "private:u1",
                    "ts": datetime.now(timezone.utc).timestamp() - 5,
                    "sender_id": "u1",
                    "sender_name": "小明",
                    "text": "请发一个表情包",
                }
            },
        )
        event = SimpleNamespace(
            message_str="现在呢",
            message_obj=SimpleNamespace(message_id="m1"),
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        request = SimpleNamespace(
            system_prompt="旧提示\n<!-- private_companion_capability_hub_v1 -->",
            func_tool=SimpleNamespace(tools=[]),
        )

        await append_capability_guidance(plugin, event, request)

        self.assertIn("emoji", request._private_companion_capability_decision.actions)
        self.assertIn("请发一个表情包", request.system_prompt)

    async def test_request_does_not_advertise_unavailable_search(self) -> None:
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "private:u1",
            _recall_message_cache={},
        )
        event = SimpleNamespace(
            message_str="查一下今天的天气",
            message_obj=SimpleNamespace(message_id="m1"),
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        request = SimpleNamespace(system_prompt="原有提示", func_tool=None)

        await append_capability_guidance(plugin, event, request)

        self.assertNotIn("search", request._private_companion_capability_decision.actions)

    async def test_injects_private_recent_context_without_other_scope_leak(
        self,
    ) -> None:
        now = datetime.now(timezone.utc).timestamp()
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "private:u1",
            _extract_group_id_from_event=lambda _event: "",
            _get_group=lambda _group_id: {},
            _recall_message_cache={
                "m1": {
                    "message_id": "m1",
                    "scope": "private:u1",
                    "ts": now - 60,
                    "sender_id": "u1",
                    "sender_name": "小明",
                    "text": "我们刚才说到橘猫",
                },
                "other": {
                    "message_id": "other",
                    "scope": "private:u2",
                    "ts": now - 30,
                    "sender_id": "u2",
                    "sender_name": "小红",
                    "text": "不能泄露这条消息",
                },
            },
        )
        event = SimpleNamespace(
            message_str="它多大了",
            message_obj=SimpleNamespace(message_id="m2"),
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        request = SimpleNamespace(system_prompt="原有私聊提示")

        await append_capability_guidance(plugin, event, request)

        self.assertIn("我们刚才说到橘猫", request.system_prompt)
        self.assertNotIn("不能泄露这条消息", request.system_prompt)
        self.assertIn("私聊", request.system_prompt)

    async def test_private_context_reuses_cached_image_description_only(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        summary_getter = AsyncMock(return_value="一只橘猫在窗台上")
        plugin = SimpleNamespace(
            _event_scope_key=lambda _event: "private:u1",
            _extract_group_id_from_event=lambda _event: "",
            _get_group=lambda _group_id: {},
            _context_image_cached_summary=summary_getter,
            _recall_message_cache={
                "m1": {
                    "message_id": "m1",
                    "scope": "private:u1",
                    "ts": now - 60,
                    "sender_id": "u1",
                    "sender_name": "小明",
                    "text": "[图片]",
                    "images": ["opaque-image-reference"],
                    "image_items": [{"source": "opaque-image-reference"}],
                },
                "m2": {
                    "message_id": "m2",
                    "scope": "private:u1",
                    "ts": now,
                    "sender_id": "u1",
                    "sender_name": "小明",
                    "text": "那只猫呢",
                },
            },
        )
        event = SimpleNamespace(
            message_str="它多大了",
            message_obj=SimpleNamespace(message_id="m2"),
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "小明",
        )
        request = SimpleNamespace(system_prompt="原有私聊提示")

        await append_capability_guidance(plugin, event, request)

        self.assertIn("一只橘猫在窗台上", request.system_prompt)
        summary_getter.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
