# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class HubNoPreReplyWaitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        self.plugin._central_hub_scheduler_enabled = True
        self.plugin.enable_message_debounce = True
        self.plugin.enable_smart_message_debounce = True
        self.plugin.text_message_debounce_seconds = 5.0
        self.plugin.image_message_debounce_seconds = 8.0
        self.plugin.group_high_intensity_merge_seconds = 8.0
        self.plugin.group_wakeup_short_text_wait_seconds = 5.0
        self.plugin.enable_group_wakeup_enhancement = True

    def test_normal_messages_have_no_pre_reply_wait(self) -> None:
        self.assertEqual(0, self.plugin._message_debounce_seconds("text"))
        self.assertEqual(0, self.plugin._message_debounce_seconds("group"))
        self.assertEqual(0, self.plugin._message_debounce_seconds("forward"))
        self.assertFalse(self.plugin._smart_message_debounce_enabled())
        self.assertEqual(0, self.plugin._group_high_intensity_merge_wait_seconds())

    def test_single_image_handoff_does_not_wait_old_fixed_window(self) -> None:
        self.assertLess(self.plugin._message_debounce_seconds("image"), 0.01)

    def test_old_debounce_switch_cannot_disable_image_handoff(self) -> None:
        self.plugin.enable_message_debounce = False
        self.plugin._semantic_message_buffers = {}
        self.plugin._note_semantic_message_buffer(
            "private:u1:u1", "[图片]", wait_seconds=0.001, kind="image"
        )
        self.assertIn("private:u1:u1", self.plugin._semantic_message_buffers)

    def test_short_group_wakeup_has_no_fixed_wait(self) -> None:
        event = SimpleNamespace(
            private_companion_group_scene={"talking_to": "bot", "trigger": "at_bot"}
        )
        self.assertEqual(0, self.plugin._group_short_wakeup_wait_seconds(event, "小七"))


if __name__ == "__main__":
    unittest.main()
