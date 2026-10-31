from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from astrbot.api.message_components import Record
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _Harness(TtsEnhancementMixin):
    def __init__(self):
        self.tts_frequency_control_mode = "global"
        self.tts_session_min_interval_seconds = 90.0
        self.tts_voice_language = "zh"
        self.tts_delivery_mode = "voice_only"
        self._tts_session_last_at = {}
        provider = SimpleNamespace(provider_config={"type": "edge", "id": "voice"})
        self.context = SimpleNamespace(
            get_config=lambda *_: {},
            get_using_tts_provider=lambda *_: provider,
        )
        self._tts_record_component = AsyncMock(return_value=Record(file="voice.wav"))


def _event(origin, text="继续聊吧", actions=None):
    event = SimpleNamespace(
        unified_msg_origin=origin, message_str=text, get_result=lambda: None
    )
    if actions is not None:
        event._private_companion_capability_decision = SimpleNamespace(actions=actions)
    return event


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["GroupMessage", "FriendMessage"])
@pytest.mark.parametrize(
    "actions,text", [(("voice",), "唱得有点难听"), (None, "再发一条语音")]
)
async def test_consecutive_requested_voice_is_not_dropped_by_auto_cooldown(
    scope, actions, text
):
    harness = _Harness()
    origin = f"Nahida:{scope}:123"
    first = _event(origin, "用语音回答", actions)
    second = _event(origin, text, actions)
    with patch(
        "astrbot_plugin_private_companion.tts_enhancement.time.time", return_value=1000
    ):
        first_chain = await harness._process_tts_tags("<tts>第一条。</tts>", first)
    with patch(
        "astrbot_plugin_private_companion.tts_enhancement.time.time", return_value=1010
    ):
        second_chain = await harness._process_tts_tags("<tts>第二条。</tts>", second)
    assert any(isinstance(item, Record) for item in first_chain)
    assert any(isinstance(item, Record) for item in second_chain)
    assert harness._tts_record_component.await_count == 2
    assert harness._tts_session_last_at[origin] == 1010


@pytest.mark.asyncio
async def test_auto_voice_cooldown_applies_between_replies_not_between_blocks():
    harness = _Harness()
    event = _event("Nahida:GroupMessage:123")
    with patch(
        "astrbot_plugin_private_companion.tts_enhancement.time.time", return_value=1000
    ):
        chain = await harness._process_tts_tags(
            "<tts>第一段。</tts><tts>第二段。</tts>", event
        )
    assert sum(isinstance(item, Record) for item in chain) == 2
    with patch(
        "astrbot_plugin_private_companion.tts_enhancement.time.time", return_value=1010
    ):
        next_chain = await harness._process_tts_tags(
            "<tts>自动语音。</tts>", _event(event.unified_msg_origin)
        )
    assert not any(isinstance(item, Record) for item in next_chain)
    assert harness._tts_record_component.await_count == 2


@pytest.mark.asyncio
async def test_failed_synthesis_does_not_start_cooldown():
    harness = _Harness()
    event = _event("Nahida:GroupMessage:123")
    harness._tts_record_component.return_value = None
    await harness._process_tts_tags("<tts>第一条。</tts>", event)
    assert harness._tts_session_last_at == {}
    harness._tts_record_component.return_value = Record(file="retry.wav")
    chain = await harness._process_tts_tags("<tts>重试。</tts>", event)
    assert any(isinstance(item, Record) for item in chain)


def test_unapproved_auto_voice_still_obeys_session_and_bot_isolation():
    harness = _Harness()
    origin = "Nahida:GroupMessage:123"
    harness._tts_session_last_at[origin] = 990
    with patch(
        "astrbot_plugin_private_companion.tts_enhancement.time.time", return_value=1000
    ):
        assert harness._tts_session_interval_remaining(_event(origin)) == 80
        assert (
            harness._tts_session_interval_remaining(_event("likh:GroupMessage:123"))
            == 0
        )
        assert (
            harness._tts_session_interval_remaining(_event("Nahida:FriendMessage:123"))
            == 0
        )
        assert (
            harness._tts_session_interval_remaining(_event(origin, "发语音", ())) == 80
        )
        forced = _event(origin)
        forced._private_companion_tts_forced_by_message_scope = True
        assert harness._tts_session_interval_remaining(forced) == 80


@pytest.mark.parametrize("request_kind", ["explicit", "hub"])
def test_strong_constraints_allow_authorized_consecutive_voice(request_kind):
    harness = _Harness()
    harness.tts_generation_mode = "fast_tag"
    harness.tts_constraint_mode = "strong"
    origin = "Nahida:GroupMessage:123"
    harness._tts_session_last_at[origin] = 990
    event = _event(
        origin,
        "再发一条语音" if request_kind == "explicit" else "继续聊吧",
        ("voice",) if request_kind == "hub" else None,
    )
    with patch(
        "astrbot_plugin_private_companion.tts_enhancement.time.time", return_value=1000
    ):
        assert (
            harness._tts_strong_constraint_block_reason(event, check_probability=False)
            == ""
        )
