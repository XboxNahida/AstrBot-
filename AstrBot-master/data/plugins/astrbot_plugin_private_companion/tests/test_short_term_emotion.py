import unittest

from astrbot_plugin_private_companion import short_term_emotion as emotion


class ShortTermEmotionTests(unittest.TestCase):
    def settle(self, prior=None, *, event_id="m1", now=1000, kind="provoke", **kw):
        return emotion.advance_emotion(
            prior,
            {
                "kind": kind,
                "intensity": 0.6,
                "confidence": 0.95,
                "targets_bot": True,
                "reason": "反复故意顶嘴",
                "topic": "午饭",
                **kw,
            },
            event_id=event_id,
            now=now,
        )

    def test_repeated_provocation_accumulates_but_duplicate_does_not(self):
        first = self.settle()
        duplicate = self.settle(first, now=1001)
        second = self.settle(first, event_id="m2", now=1001)
        self.assertEqual(first, duplicate)
        self.assertGreater(second["irritation"], first["irritation"])

    def test_disagreement_is_not_provocation(self):
        state = self.settle(kind="disagree")
        self.assertEqual(0, state["irritation"])

    def test_apology_and_time_cool_down_without_instant_reset(self):
        hot = self.settle()
        calm = self.settle(hot, event_id="m2", now=1001, kind="apology")
        self.assertGreater(calm["irritation"], 0)
        self.assertLess(calm["irritation"], hot["irritation"])
        cold = emotion.advance_emotion(hot, {}, event_id="m3", now=4600)
        self.assertLess(cold["irritation"], 0.001)

    def test_topic_change_reduces_residue_and_teasing_is_not_hurt(self):
        hot = self.settle()
        changed = self.settle(hot, event_id="m2", now=1001, kind="topic_change")
        self.assertLess(changed["irritation"], hot["irritation"])
        shy = self.settle(kind="tease", phase="flustered")
        self.assertGreater(shy["embarrassment"], 0)
        self.assertEqual(0, shy["irritation"])
        self.assertEqual("flustered", shy["phase"])

    def test_unrelated_and_uncertain_events_do_not_change_emotion(self):
        for signal in ({"targets_bot": False}, {"confidence": 0.1}):
            state = self.settle(**signal)
            self.assertEqual(0, state["irritation"])

    def test_malformed_and_extreme_inputs_are_bounded(self):
        state = self.settle(intensity=float("inf"), recovery_seconds="bad")
        self.assertEqual(0, state["irritation"])
        state = self.settle(intensity=999, reason="甲" * 1000)
        for index in range(300):
            state = self.settle(state, event_id=str(index), now=1000 + index)
        self.assertLessEqual(state["irritation"], 1)
        self.assertLessEqual(len(state["event_ids"]), 64)
        self.assertLessEqual(len(state["reason"]), 160)

    def test_persona_adjusted_intensity_changes_response_without_fixed_personality(
        self,
    ):
        sensitive = self.settle(intensity=0.9)
        tolerant = self.settle(intensity=0.1)
        self.assertGreater(sensitive["irritation"], tolerant["irritation"])


if __name__ == "__main__":
    unittest.main()
