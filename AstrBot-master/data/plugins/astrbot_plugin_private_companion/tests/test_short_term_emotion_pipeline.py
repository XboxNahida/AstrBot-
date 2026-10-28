import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.capability_hub import (
    CapabilityDecision,
    build_capability_context,
    parse_model_capability_decision,
)
from astrbot_plugin_private_companion.passive_state_pipeline import (
    append_capability_guidance,
)


class EmotionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_robot_profile_persona_reaches_hub_and_emotion_state(self):
        from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
        from astrbot.core.persona_mgr import PersonaManager
        from astrbot.core.umop_config_router import UmopConfigRouter

        router = UmopConfigRouter(SimpleNamespace())
        router.umop_to_conf_id = {"likh:*:*": "late-profile"}
        configs = object.__new__(AstrBotConfigManager)
        configs.ucr = router
        configs.abconf_data = {"late-profile": {"path": "unused", "name": "晚柠配置"}}
        configs.confs = {
            "default": {"provider_settings": {"default_personality": "小七"}},
            "late-profile": {"provider_settings": {"default_personality": "晚柠"}},
        }
        manager = object.__new__(PersonaManager)
        manager.personas_v3 = [
            {"name": "小七", "prompt": "嘴硬心软"},
            {"name": "晚柠", "prompt": "温柔贴心"},
        ]
        plugin = SimpleNamespace(
            enable_short_term_emotion=True,
            context=SimpleNamespace(persona_manager=manager),
            _llm_request_provider_settings_for_event=lambda event: configs.get_conf(
                event.unified_msg_origin
            )["provider_settings"],
        )
        judge = AsyncMock(
            return_value=CapabilityDecision(True, "text", "neutral", (), False, "test")
        )
        with (
            patch("astrbot.core.persona_mgr.sp.get_async", AsyncMock(return_value={})),
            patch(
                "astrbot_plugin_private_companion.passive_state_pipeline.judge_capabilities",
                judge,
            ),
        ):
            for bot, prompt in (("Nahida", "嘴硬心软"), ("likh", "温柔贴心")):
                event = SimpleNamespace(
                    unified_msg_origin=f"{bot}:FriendMessage:same-user",
                    message_str="你好",
                    is_private_chat=lambda: True,
                    get_sender_id=lambda: "same-user",
                    get_sender_name=lambda: "同一用户",
                    get_platform_name=lambda: "qq_official",
                )
                request = SimpleNamespace(
                    system_prompt="",
                    conversation=SimpleNamespace(persona_id=None, cid="same-cid"),
                )
                await append_capability_guidance(plugin, event, request)
                self.assertEqual(prompt, judge.await_args.kwargs["persona_prompt"])
        self.assertEqual(2, len(plugin._short_term_emotion_states))
        self.assertEqual(
            {"小七", "晚柠"}, {key[0] for key in plugin._short_term_emotion_states}
        )

    async def test_folded_scope_does_not_share_emotion_between_platforms_or_conversations(
        self,
    ):
        plugin = SimpleNamespace(
            enable_short_term_emotion=True, _event_scope_key=lambda event: "private:u1"
        )
        signal = {
            "kind": "provoke",
            "intensity": 1,
            "confidence": 1,
            "targets_bot": True,
        }
        judge = AsyncMock(
            return_value=CapabilityDecision(
                True, "text", "neutral", (), False, "test", emotion_event=signal
            )
        )
        with patch(
            "astrbot_plugin_private_companion.passive_state_pipeline.judge_capabilities",
            judge,
        ):
            for index, (umo, cid) in enumerate(
                (
                    ("qq:private:u1", "c1"),
                    ("wx:private:u1", "c1"),
                    ("qq:private:u1", "c2"),
                )
            ):
                event = SimpleNamespace(
                    unified_msg_origin=umo,
                    message_str="你又错了",
                    message_obj=SimpleNamespace(message_id=f"m{index}"),
                    is_private_chat=lambda: True,
                    get_sender_id=lambda: "u1",
                    get_sender_name=lambda: "甲",
                )
                req = SimpleNamespace(
                    system_prompt="人格",
                    conversation=SimpleNamespace(persona_id="a", cid=cid),
                )
                await append_capability_guidance(plugin, event, req)
                self.assertEqual(
                    0.45, event._private_companion_short_emotion["irritation"]
                )
        self.assertEqual(3, len(plugin._short_term_emotion_states))

    async def test_persona_sender_session_isolation_retry_and_prompt_replacement(self):
        resolver = AsyncMock(
            side_effect=lambda **kw: (
                kw["conversation_persona_id"],
                {"prompt": "相同人格文字"},
                None,
                False,
            )
        )
        plugin = SimpleNamespace(
            enable_short_term_emotion=True,
            context=SimpleNamespace(
                persona_manager=SimpleNamespace(resolve_selected_persona=resolver)
            ),
            _event_scope_key=lambda event: event.unified_msg_origin,
            _recall_message_cache={},
        )
        signal = {
            "kind": "provoke",
            "intensity": 1,
            "confidence": 1,
            "targets_bot": True,
        }
        judge = AsyncMock(
            return_value=CapabilityDecision(
                True, "text", "neutral", (), False, "test", emotion_event=signal
            )
        )
        with patch(
            "astrbot_plugin_private_companion.passive_state_pipeline.judge_capabilities",
            judge,
        ):
            states = []
            for user, scope, persona in (
                ("u1", "private:u1", "a"),
                ("u2", "private:u1", "a"),
                ("u1", "group:g1", "a"),
                ("u1", "private:u1", "b"),
            ):
                event = SimpleNamespace(
                    unified_msg_origin=scope,
                    message_str="你又错了",
                    message_obj=SimpleNamespace(message_id="m1"),
                    is_private_chat=lambda: True,
                    get_sender_id=lambda: user,
                    get_sender_name=lambda: user,
                    get_platform_name=lambda: "test",
                )
                req = SimpleNamespace(
                    system_prompt="人格",
                    conversation=SimpleNamespace(persona_id=persona),
                )
                await append_capability_guidance(plugin, event, req)
                first = event._private_companion_short_emotion["irritation"]
                await append_capability_guidance(plugin, event, req, force_rejudge=True)
                self.assertEqual(
                    first, event._private_companion_short_emotion["irritation"]
                )
                self.assertEqual(1, req.system_prompt.count("【本轮短期互动状态"))
                states.append(first)
            self.assertEqual(4, len(plugin._short_term_emotion_states))
            self.assertEqual([0.45] * 4, states)
            self.assertIn("相同人格文字", judge.await_args.kwargs["persona_prompt"])

    async def test_early_hub_resolves_the_current_conversation_persona(self):
        conversation = SimpleNamespace(persona_id="selected")
        resolver = AsyncMock(
            return_value=("selected", {"prompt": "当前会话人格"}, None, False)
        )
        plugin = SimpleNamespace(
            enable_short_term_emotion=True,
            context=SimpleNamespace(
                persona_manager=SimpleNamespace(resolve_selected_persona=resolver),
                conversation_manager=SimpleNamespace(
                    get_curr_conversation_id=AsyncMock(return_value="c1"),
                    get_conversation=AsyncMock(return_value=conversation),
                ),
            ),
        )
        event = SimpleNamespace(
            unified_msg_origin="private:u1",
            message_str="你好",
            is_private_chat=lambda: True,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "甲",
            get_platform_name=lambda: "test",
        )
        judge = AsyncMock(
            return_value=CapabilityDecision(True, "text", "neutral", (), False, "test")
        )
        with patch(
            "astrbot_plugin_private_companion.passive_state_pipeline.judge_capabilities",
            judge,
        ):
            await append_capability_guidance(plugin, event, None)
        self.assertEqual(
            "selected", resolver.await_args.kwargs["conversation_persona_id"]
        )
        self.assertEqual("当前会话人格", judge.await_args.kwargs["persona_prompt"])

    def test_malformed_model_enum_fields_are_ignored(self):
        context = build_capability_context(
            scope="private:u1",
            current_text="hi",
            sender_id="u1",
            sender_name="甲",
            now=datetime.now(timezone.utc),
        )
        for malformed in ([], {}, 123, None):
            for field in ("route", "kind", "phase"):
                with self.subTest(field=field, malformed=malformed):
                    data = {
                        "reply": True,
                        "actions": [],
                        "emotion_event": {"kind": "tease", "phase": "steady"},
                    }
                    target = data if field == "route" else data["emotion_event"]
                    target[field] = malformed
                    decision = parse_model_capability_decision(
                        json.dumps(data), context=context, available_capabilities=()
                    )
                    self.assertIsNotNone(decision)
