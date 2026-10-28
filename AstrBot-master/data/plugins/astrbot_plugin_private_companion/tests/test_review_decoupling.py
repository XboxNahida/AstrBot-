# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime

from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _PassiveReviewHarness(UserMemoryMixin):
    enable_passive_response_review = False
    enable_response_self_review = True
    passive_review_mode = "full"
    passive_review_strength = "strict"
    response_review_provider_id = "review"
    mai_style_provider_id = ""
    response_review_max_chars = 260

    def __init__(self) -> None:
        self.llm_called = False

    @staticmethod
    def _environment_now() -> datetime:
        return datetime(2026, 7, 15, 15, 30)

    async def _llm_call(self, *_args, **_kwargs):
        self.llm_called = True
        return "不应调用"


class _LenientPassiveReviewHarness(_PassiveReviewHarness):
    enable_passive_response_review = True
    passive_review_strength = "lenient"

    @staticmethod
    def _response_review_flags(*_args, **_kwargs):
        return ["repeats_last_bot_message"]

    async def _llm_call(self, *_args, **_kwargs):
        self.llm_called = True
        return self._response_review_drop_marker()

    @staticmethod
    async def _resolve_proactive_persona_prompt(_user):
        return ""

    @staticmethod
    def _format_private_fact_attribution_guard(*_args, **_kwargs):
        return ""

    @staticmethod
    def _format_reply_style_prompt():
        return ""

    @staticmethod
    def _task_provider(*_args):
        return "review"


class _ActivePassiveReviewHarness(_PassiveReviewHarness):
    enable_passive_response_review = True
    passive_review_mode = "full"

    def __init__(self, flags):
        super().__init__()
        self.flags = flags
        self.prompts = []

    def _response_review_flags(self, *_args, **_kwargs):
        return list(self.flags)

    async def _llm_call(self, prompt, **_kwargs):
        self.llm_called = True
        self.prompts.append(prompt)
        return "已修正的回复"

    @staticmethod
    async def _resolve_proactive_persona_prompt(_user):
        return "当前人格：傲娇损友"

    @staticmethod
    def _format_private_fact_attribution_guard(*_args, **_kwargs):
        return "事实归属必须准确"

    @staticmethod
    def _format_reply_style_prompt():
        return "插件风格：必须简短克制"

    @staticmethod
    def _task_provider(*_args):
        return "review"


class ReviewDecouplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabling_passive_review_skips_model_even_when_legacy_alias_is_on(self):
        harness = _PassiveReviewHarness()

        result = await harness._review_and_rewrite_response({}, "继续讲", "这是正常回复。")

        self.assertEqual(result, "这是正常回复。")
        self.assertFalse(harness.llm_called)

    async def test_lenient_passive_review_does_not_drop_reply(self):
        harness = _LenientPassiveReviewHarness()
        user = {"last_companion_message": "上一条回复"}

        result = await harness._review_and_rewrite_response(user, "继续讲", "这是正常回复。")

        self.assertEqual(result, "这是正常回复。")
        self.assertTrue(harness.llm_called)

    async def test_soft_style_flags_do_not_trigger_a_second_model_call(self):
        harness = _ActivePassiveReviewHarness(
            [
                "too_long",
                "casual_overexplained",
                "weather_overexplained",
                "assistant_tone",
                "over_structured",
                "unnatural_punctuation",
                "expression_overfit",
                "copied_user_expression_sample",
            ]
        )
        response = "这是人格自然生成的口语回复，内容本身没有安全或事实问题。"

        result = await harness._review_and_rewrite_response(
            {}, "帮我详细解释一下", response
        )

        self.assertEqual(response, result)
        self.assertFalse(harness.llm_called)

    async def test_soft_style_flags_do_not_enter_severe_only_mode(self):
        harness = _ActivePassiveReviewHarness(
            ["casual_overexplained", "unnatural_punctuation"]
        )
        harness.passive_review_mode = "severe_only"

        result = await harness._review_and_rewrite_response(
            {}, "嗯嗯", "就按你说的来呀。"
        )

        self.assertEqual("就按你说的来呀。", result)
        self.assertFalse(harness.llm_called)
        self.assertEqual(
            [],
            harness._response_review_severe_flags(
                ["too_long", "over_structured", "casual_overexplained", "unnatural_punctuation"]
            ),
        )

    async def test_internal_leak_flag_still_triggers_targeted_review(self):
        harness = _ActivePassiveReviewHarness(["leaks_internal"])

        result = await harness._review_and_rewrite_response(
            {}, "帮我详细解释一下", "我现在的内部规划是这样。"
        )

        self.assertEqual("已修正的回复", result)
        self.assertTrue(harness.llm_called)
        self.assertIn("事实归属必须准确", harness.prompts[0])
        self.assertNotIn("插件风格：必须简短克制", harness.prompts[0])


if __name__ == "__main__":
    unittest.main()
