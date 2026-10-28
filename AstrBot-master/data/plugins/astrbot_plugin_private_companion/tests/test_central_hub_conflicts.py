# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class CentralHubConflictTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_hub_decision_prevents_send_time_silence_judge(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin._central_hub_scheduler_enabled = True
        plugin._decide_smart_silence = AsyncMock(
            side_effect=AssertionError("second semantic judge")
        )
        event = SimpleNamespace(
            _private_companion_capability_decision=SimpleNamespace(reply=True),
        )

        await plugin.suppress_smart_silence_reply_before_send(event)

        plugin._decide_smart_silence.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
