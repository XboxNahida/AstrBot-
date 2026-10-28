from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin


class _LimitHarness(ProactiveMixin):
    proactive_intensity_preset = "high_group"
    group_interject_max_daily = 999_999
    group_interject_min_interval_minutes = 10


class _InterjectionHarness(GroupObservationMixin):
    enable_group_interjection = True
    group_interject_max_daily = 999_999
    group_interject_min_interval_minutes = 7

    @staticmethod
    def _effective_group_interject_max_daily() -> int:
        return 999_999

    @staticmethod
    def _effective_group_interject_min_interval_minutes() -> int:
        return 7

    @staticmethod
    def _proactive_daily_limit_is_unlimited(value: int) -> bool:
        return value >= 999_999

    @staticmethod
    def _filtered_group_recent_messages(group: dict) -> list[dict]:
        return group.get("recent_messages", [])

    @staticmethod
    def _cycle_group_interject_probability(value: float) -> float:
        return value

    @staticmethod
    def _group_topic_signature(text: str) -> str:
        if "学习资料" in text:
            return "学习资料"
        return text

    @staticmethod
    def _topic_signature_similar(left: str, right: str) -> bool:
        return bool(left and right and left == right)


class ActiveGroupInterjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _InterjectionHarness()
        self.group = {
            "group_id": "group-1",
            "interject_today": 999_999,
            "last_interject_at": 0,
            "atmosphere": {"pace": "有来有回", "mood": "平稳"},
            "recent_messages": [
                {"sender_id": "user-a", "talking_to": "group", "text": "学习资料怎么整理更方便"},
            ],
        }

    def test_unlimited_setting_overrides_high_group_preset(self) -> None:
        self.assertEqual(999_999, _LimitHarness()._effective_group_interject_max_daily())

    def test_unlimited_setting_uses_configured_interjection_interval(self) -> None:
        self.assertEqual(10, _LimitHarness()._effective_group_interject_min_interval_minutes())

    def test_academic_question_ignores_legacy_probability_and_limits(self) -> None:
        self.group["last_interject_at"] = time.time()
        with patch("astrbot_plugin_private_companion.group_observation.random.random", side_effect=AssertionError("random draw")):
            allowed, _ = self.plugin._group_interjection_allowed(self.group, "量子纠缠为什么不会超光速传递信息？")
        self.assertTrue(allowed)

    def test_rejection_silences_current_topic_then_new_topic_recovers(self) -> None:
        self.group["last_bot_interjection"] = {
            "ts": time.time(),
            "topic_signature": "学习资料",
            "text": "可以按科目分类。",
        }
        self.plugin._update_group_interjection_feedback(
            self.group, sender_id="user-a", text="小七别插话了"
        )
        with patch("astrbot_plugin_private_companion.group_observation.random.random", side_effect=AssertionError("random draw")):
            same_topic, _ = self.plugin._group_interjection_allowed(self.group, "学习资料再分几个文件夹")
            self.group["recent_messages"][-1]["sender_id"] = "user-b"
            other_person, _ = self.plugin._group_interjection_allowed(self.group, "学习资料再分几个文件夹")
            self.group["recent_messages"][-1]["sender_id"] = "user-a"
            new_topic, _ = self.plugin._group_interjection_allowed(self.group, "晚上吃什么好")

        self.assertFalse(same_topic)
        self.assertTrue(new_topic)
        self.assertTrue(other_person)

    def test_quoted_rejection_does_not_silence_topic(self) -> None:
        self.group["last_bot_interjection"] = {
            "ts": time.time(),
            "topic_signature": "学习资料",
            "text": "可以按科目分类。",
        }
        self.plugin._update_group_interjection_feedback(
            self.group, sender_id="user-a", text="他说让我闭嘴"
        )

        self.assertFalse(self.group.get("interjection_silences"))

    def test_short_acknowledgement_is_not_a_candidate(self) -> None:
        with patch("astrbot_plugin_private_companion.group_observation.random.random", side_effect=AssertionError("random draw")):
            allowed, _ = self.plugin._group_interjection_allowed(self.group, "嗯嗯")

        self.assertFalse(allowed)

    def test_short_interest_keyword_reaches_central_judge(self) -> None:
        self.plugin._group_wakeup_interest_words = lambda _group: ["摄影"]
        with patch("astrbot_plugin_private_companion.group_observation.random.random", side_effect=AssertionError("random draw")):
            allowed, _ = self.plugin._group_interjection_allowed(self.group, "摄影")
        self.assertTrue(allowed)

    def test_academic_answer_is_not_truncated_to_old_short_interjection_limit(self) -> None:
        answer = "量子纠缠只能产生相关性，不能让发送方控制接收方的测量结果。" * 3
        self.assertEqual(self.plugin._clean_group_interjection_reply(answer), answer)

    def test_negative_reaction_silences_only_current_speaker_and_topic(self) -> None:
        self.group["last_bot_interjection"] = {
            "ts": time.time(),
            "topic_signature": "学习资料",
            "text": "可以按科目分类。",
        }
        self.plugin._update_group_interjection_feedback(
            self.group, sender_id="user-a", text="你这接得有点尬，别这样"
        )
        same_topic, _ = self.plugin._group_interjection_allowed(self.group, "学习资料再分几个文件夹")
        self.group["recent_messages"][-1]["sender_id"] = "user-b"
        other_person, _ = self.plugin._group_interjection_allowed(self.group, "学习资料再分几个文件夹")
        self.group["recent_messages"][-1]["sender_id"] = "user-a"
        new_topic, _ = self.plugin._group_interjection_allowed(self.group, "晚上吃什么好")

        self.assertFalse(same_topic)
        self.assertTrue(other_person)
        self.assertTrue(new_topic)


class CentralGroupInterjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_hub_decision_prevents_duplicate_interjection_judge(self) -> None:
        plugin = _InterjectionHarness()
        plugin._llm_call = AsyncMock(side_effect=AssertionError("second semantic judge"))
        plugin._format_group_context_for_prompt = lambda _group: ""
        plugin._format_persona_voice_channel_prompt = lambda _mode: ""
        plugin._task_provider = lambda *_args: "provider"
        plugin.group_interject_provider_id = "provider"
        plugin.mai_style_provider_id = "provider"
        plugin._parse_group_interjection_decision = lambda _raw: (False, "", "skip")
        event = SimpleNamespace(
            get_sender_id=lambda: "user-a",
            is_wake=False,
            is_at_or_wake_command=False,
            _private_companion_capability_decision=SimpleNamespace(reply=True),
            _private_companion_group_repeat_processed=True,
            private_companion_group_scene={"talking_to": "bot"},
        )

        await plugin._maybe_group_interject(
            event,
            {"recent_messages": []},
            "继续刚刚的算法问题",
        )

        plugin._llm_call.assert_not_awaited()

    async def test_central_model_receives_question_interest_and_feedback_rules(self) -> None:
        plugin = _InterjectionHarness()
        plugin._format_group_context_for_prompt = lambda _group: "近期群聊"
        plugin._format_persona_voice_channel_prompt = lambda _mode: "自然"
        plugin._task_provider = lambda *_args: "provider"
        plugin.group_interject_provider_id = "provider"
        plugin.mai_style_provider_id = "provider"
        plugin._llm_call = AsyncMock(return_value='{"should_reply":false,"text":"","reason":"无补充"}')
        plugin._parse_group_interjection_decision = lambda _raw: (False, "", "无补充")
        event = SimpleNamespace(
            get_sender_id=lambda: "user-a",
            is_wake=False,
            is_at_or_wake_command=False,
            _private_companion_group_repeat_processed=True,
        )
        group = {"recent_messages": [{"sender_id": "user-a", "talking_to": "group"}]}

        await plugin._maybe_group_interject(event, group, "量子纠缠为什么不会超光速传递信息？")

        prompt = plugin._llm_call.await_args.args[0]
        self.assertIn("学术", prompt)
        self.assertIn("兴趣", prompt)
        self.assertIn("当前话题", prompt)


if __name__ == "__main__":
    unittest.main()
