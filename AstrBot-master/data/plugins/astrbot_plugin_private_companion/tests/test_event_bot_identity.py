import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from astrbot_plugin_private_companion.group_wakeup import GroupWakeupMixin
from astrbot_plugin_private_companion.main import _multi_persona_event_context
from astrbot_plugin_private_companion.message_pipeline import handle_group_message


class IdentityHarness(GroupWakeupMixin):
    def __init__(self):
        self.bot_name = "小七"
        self.group_wakeup_direct_words = ["小七", "帮帮忙"]
        self.group_wakeup_owner_direct_words = ["小七", "专属词"]
        self.context = SimpleNamespace(
            persona_manager=SimpleNamespace(
                personas_v3=[{"name": "小七"}, {"name": "晚柠"}],
                resolve_selected_persona=AsyncMock(side_effect=self.resolve),
            )
        )

    async def resolve(self, **kwargs):
        name = (
            kwargs["conversation_persona_id"]
            or kwargs["provider_settings"]["default_personality"]
        )
        return name, {"name": name, "prompt": f"你是{name}"}, None, False

    def _llm_request_provider_settings_for_event(self, event):
        return {"default_personality": "晚柠" if event.bot == "likh" else "小七"}

    def _event_scene_signals(self, event):
        return event.signals

    @_multi_persona_event_context
    async def observe(self, event):
        await asyncio.sleep(0)
        return (
            self.bot_name,
            self._configured_group_direct_wakeup_words(),
            self._configured_group_owner_direct_wakeup_words(),
            self._infer_group_scene(
                event, {}, sender_id="u", sender_name="群友", text=event.message_str
            ),
        )

    @_multi_persona_event_context
    async def fail(self, event):
        raise RuntimeError(self.bot_name)

    @_multi_persona_event_context
    async def stream(self, event):
        yield self.bot_name
        await asyncio.sleep(0)
        yield self.bot_name


def event(bot, text, **kwargs):
    return SimpleNamespace(
        bot=bot,
        message_str=text,
        unified_msg_origin=f"{bot}:GroupMessage:g",
        get_platform_name=lambda: "aiocqhttp",
        signals=kwargs.get("signals", {}),
    )


class EventBotIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_self_message_echo_stops_before_hub_or_observation(self):
        owner = SimpleNamespace(
            _is_onebot_poke_notice_event=lambda _: False,
            _qzone_note_event_bot=Mock(),
            _feature_enabled_or_temp_unlocked=lambda _: True,
            _extract_group_id_from_event=lambda _: "g",
            _group_enabled_for_event=lambda _: True,
            _event_self_id=lambda _: "self",
            _capture_group_observation_event=AsyncMock(),
        )
        incoming = SimpleNamespace(
            get_sender_id=lambda: "self", message_str="已发送的回复", stop_event=Mock()
        )
        await handle_group_message(owner, incoming)
        incoming.stop_event.assert_called_once()
        owner._capture_group_observation_event.assert_not_awaited()

    async def test_parallel_robots_keep_names_and_wake_words_isolated(self):
        owner = IdentityHarness()
        small, late = await asyncio.gather(
            owner.observe(event("Nahida", "小七，在吗")),
            owner.observe(event("likh", "晚柠，在吗")),
        )
        self.assertEqual("小七", small[0])
        self.assertEqual("晚柠", late[0])
        self.assertEqual(["晚柠", "帮帮忙"], late[1])
        self.assertEqual(["专属词"], late[2])
        self.assertEqual("mention_bot_name", late[3]["trigger"])
        self.assertEqual("小七", owner.bot_name)

    async def test_other_persona_name_does_not_mark_message_as_addressed(self):
        result = await IdentityHarness().observe(event("likh", "小七，在吗"))
        self.assertNotEqual("bot", result[3]["talking_to"])

    async def test_explicit_at_other_stays_other_even_with_current_name(self):
        owner = IdentityHarness()
        owner._group_member_identity_label = lambda *_a, **_k: "其他群友"
        result = await owner.observe(
            event(
                "likh",
                "晚柠",
                signals={"at_targets": [{"user_id": "other", "is_bot": False}]},
            )
        )
        self.assertEqual("at_other", result[3]["trigger"])

    async def test_current_conversation_persona_overrides_bot_default(self):
        owner = IdentityHarness()
        owner.context.conversation_manager = SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="c"),
            get_conversation=AsyncMock(return_value=SimpleNamespace(persona_id="小七")),
        )
        result = await owner.observe(event("likh", "小七"))
        self.assertEqual("小七", result[0])

    async def test_exception_restores_global_name(self):
        owner = IdentityHarness()
        with self.assertRaisesRegex(RuntimeError, "晚柠"):
            await owner.fail(event("likh", "你好"))
        self.assertEqual("小七", owner.bot_name)

    async def test_async_generator_binds_identity_for_all_segments(self):
        owner = IdentityHarness()
        self.assertEqual(
            ["晚柠", "晚柠"], [x async for x in owner.stream(event("likh", "你好"))]
        )
        self.assertEqual("小七", owner.bot_name)
