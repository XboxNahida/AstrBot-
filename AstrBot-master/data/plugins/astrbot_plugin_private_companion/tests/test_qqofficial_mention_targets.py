from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.api.message_components import At, AtAll, Reply
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    PatchedGroupMessage,
    QQOfficialPlatformAdapter,
)
from astrbot_plugin_private_companion.capability_hub import CapabilityDecision
from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.group_wakeup import GroupWakeupMixin
from astrbot_plugin_private_companion.passive_state_pipeline import (
    append_capability_guidance,
)


class MentionHarness(EventDispatchMixin, GroupWakeupMixin):
    def __init__(self, name):
        self.bot_name = name

    def _group_member_identity_label(self, user_id, name, **kwargs):
        return name or user_id


async def make_event(mentions, *, content="<@late-id>", force=False):
    raw = PatchedGroupMessage(
        None,
        "event-1",
        {
            "id": "message-1",
            "content": content,
            "author": {"member_openid": "user-1"},
            "group_openid": "group-1",
            "mentions": mentions,
            "attachments": [],
        },
    )
    message = await QQOfficialPlatformAdapter._parse_from_qqofficial(
        raw, MessageType.GROUP_MESSAGE, force_group_mention=force
    )
    return SimpleNamespace(
        message_obj=message,
        message_str=message.message_str,
        get_self_id=lambda: message.self_id,
        get_messages=lambda: message.message,
        get_platform_name=lambda: "qq_official",
    )


@pytest.mark.asyncio
async def test_same_mention_is_addressed_only_to_its_receiving_robot():
    for name, is_you, expected in (
        ("小七", False, "at_other"),
        ("晚柠", True, "at_bot"),
    ):
        event = await make_event(
            [{"id": "late-id", "username": "晚柠", "is_you": is_you}]
        )
        owner = MentionHarness(name)
        scene = owner._infer_group_scene(
            event, {}, sender_id="user-1", sender_name="用户", text=event.message_str
        )
        assert scene["trigger"] == expected
        assert owner._group_message_explicitly_ats_bot(event) is is_you
        assert len(scene["at_targets"]) == 1


@pytest.mark.asyncio
async def test_raw_mention_without_metadata_is_preserved_as_other_target():
    event = await make_event([], content="<@!other-id> 小七")
    owner = MentionHarness("小七")
    scene = owner._infer_group_scene(
        event, {}, sender_id="user-1", sender_name="用户", text=event.message_str
    )
    assert scene["trigger"] == "at_other"
    assert scene["at_targets"][0]["user_id"] == "other-id"


@pytest.mark.asyncio
async def test_official_false_is_you_beats_an_identical_display_name():
    event = await make_event([{"id": "late-id", "username": "小七", "is_you": False}])
    event.message_obj.message.insert(0, At(qq="late-id", name="小七"))
    targets = MentionHarness("小七")._event_scene_signals(event)["at_targets"]
    assert targets == [{"user_id": "late-id", "name": "小七", "is_bot": False}]


@pytest.mark.asyncio
async def test_multiple_mentions_keep_self_and_other_without_duplicates():
    event = await make_event(
        [
            {"id": "late-id", "username": "晚柠", "is_you": True},
            {"id": "other-id", "username": "小七", "is_you": False},
        ],
        content="<@late-id> <@other-id> 你们好",
    )
    targets = MentionHarness("晚柠")._event_scene_signals(event)["at_targets"]
    assert [(row["user_id"], row["is_bot"]) for row in targets] == [
        ("late-id", True),
        ("other-id", False),
    ]


@pytest.mark.asyncio
async def test_legacy_at_event_without_metadata_still_addresses_self():
    event = await make_event([], content="你好", force=True)
    assert MentionHarness("小七")._group_message_explicitly_ats_bot(event)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override", [None, "group_wakeup_resting_mention", "group_wakeup_image"]
)
@pytest.mark.parametrize("force_rejudge", [False, True])
async def test_official_other_target_cannot_be_overridden_by_wakeup_or_cache(
    override, force_rejudge
):
    event = await make_event([{"id": "late-id", "username": "晚柠", "is_you": False}])
    owner = MentionHarness("小七")
    owner._llm_call = AsyncMock(return_value='{"reply":true,"actions":[]}')
    event.is_private_chat = lambda: False
    event.is_at_or_wake_command = True
    event.is_wake = True
    event.private_companion_group_scene = owner._infer_group_scene(
        event, {}, sender_id="user-1", sender_name="用户", text=event.message_str
    )
    if override:
        event.private_companion_group_scene.update(trigger=override, talking_to="bot")
    cached = CapabilityDecision(True, "text", "neutral", (), False, "cached")
    event._private_companion_capability_decision = cached
    request = SimpleNamespace(
        system_prompt="", _private_companion_capability_decision=cached
    )
    await append_capability_guidance(owner, event, request, force_rejudge=force_rejudge)
    assert not event._private_companion_capability_decision.reply
    assert not request._private_companion_capability_decision.reply
    assert not event.is_at_or_wake_command
    assert not event.is_wake
    owner._llm_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_other", [False, True])
async def test_official_self_target_still_reaches_hub(with_other):
    mentions = [{"id": "late-id", "username": "晚柠", "is_you": True}]
    if with_other:
        mentions.append({"id": "other-id", "username": "小七", "is_you": False})
    event = await make_event(mentions)
    owner = MentionHarness("晚柠")
    owner._llm_call = AsyncMock(return_value='{"reply":true,"actions":[]}')
    owner._task_provider = lambda *_: "hub"
    event.is_private_chat = lambda: False
    event.private_companion_group_scene = owner._infer_group_scene(
        event, {}, sender_id="user-1", sender_name="用户", text=event.message_str
    )
    await append_capability_guidance(owner, event, None)
    assert event._private_companion_capability_decision.reply
    owner._llm_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_original_mention_survives_loss_of_all_scene_target_fields():
    event = await make_event([{"id": "late-id", "username": "晚柠", "is_you": False}])
    owner = MentionHarness("小七")
    owner._llm_call = AsyncMock(return_value='{"reply":true,"actions":[]}')
    event.is_private_chat = lambda: False
    event.private_companion_group_scene = {"trigger": "group_wakeup_image"}
    await append_capability_guidance(owner, event, None)
    assert not event._private_companion_capability_decision.reply
    owner._llm_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_to_self", [False, True])
async def test_original_reply_recipient_survives_wakeup_scene_override(reply_to_self):
    event = await make_event([], content="你好")
    event.get_self_id = lambda: "self-id"
    event.message_obj.message.insert(
        0, Reply(id="quoted", sender_id="self-id" if reply_to_self else "other-id")
    )
    event.is_private_chat = lambda: False
    event.private_companion_group_scene = {"trigger": "group_wakeup_image"}
    owner = MentionHarness("小七")
    owner._task_provider = lambda *_: "hub"
    owner._llm_call = AsyncMock(return_value='{"reply":true,"actions":[]}')
    await append_capability_guidance(owner, event, None)
    assert event._private_companion_capability_decision.reply is reply_to_self
    assert owner._llm_call.await_count == int(reply_to_self)


@pytest.mark.asyncio
async def test_at_all_with_other_target_includes_current_robot():
    event = await make_event([{"id": "late-id", "username": "晚柠", "is_you": False}])
    event.message_obj.message.insert(0, AtAll())
    event.is_private_chat = lambda: False
    owner = MentionHarness("小七")
    owner._task_provider = lambda *_: "hub"
    owner._llm_call = AsyncMock(return_value='{"reply":true,"actions":[]}')
    event.private_companion_group_scene = owner._infer_group_scene(
        event, {}, sender_id="user-1", sender_name="用户", text=event.message_str
    )
    await append_capability_guidance(owner, event, None)
    assert event._private_companion_capability_decision.reply
    owner._llm_call.assert_awaited_once()
