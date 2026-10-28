from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_private_companion.forward_message import ForwardMessageMixin
from astrbot_plugin_private_companion.group_member_memory import (
    list_scoped_member_scopes,
)
from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.scoped_runtime_view import (
    overlay_group_member_memory,
)
from astrbot_plugin_private_companion.worldbook import WorldbookMixin


def test_disabled_and_missing_profiles_never_restore_legacy_memory():
    base = {
        "group_episodes": [{"summary": "共享话题"}],
        "members": {
            "a": {
                "name": "名片",
                "identity_note": "旧身份",
                "important_memories": ["旧记忆"],
            },
        },
    }
    view = overlay_group_member_memory(
        base, sender_id="a", profile={"enabled": False, "identity_note": "禁用身份"}
    )
    assert "identity_note" not in view["members"]["a"]
    assert "important_memories" not in view["members"]["a"]
    assert not view["_scoped_group_member_memories"]
    view = overlay_group_member_memory(
        base, sender_id="a", profile=None, scoped_profiles={"b": {"name": "小明"}}
    )
    assert view["_scoped_group_member_memories"]["b"]["name"] == "小明"
    assert "important_memories" not in view["members"]["a"]
    assert base["members"]["a"]["identity_note"] == "旧身份"
    assert view["group_episodes"] == base["group_episodes"]


def test_current_observation_never_reads_legacy_identity_or_rename_history():
    class Harness(WorldbookMixin, GroupObservationMixin):
        _group_display_name_address_conflict = staticmethod(lambda *_a: False)
        _group_text_blocked_by_injection_guard = staticmethod(lambda *_a: False)

    host = Harness()
    base = {
        "members": {
            "a": {
                "name": "当前名片",
                "identity_name": "LEGACY_IDENTITY",
                "display_name_events": [{"old": "LEGACY_RENAME", "new": "当前名片"}],
                "recent_phrases": ["LEGACY_PHRASE"],
            }
        }
    }
    for view in (base, overlay_group_member_memory(base, sender_id="a", profile=None)):
        prompt = host._format_current_group_member_observation_for_prompt(
            view, "a", "你好"
        )
        assert "LEGACY" not in prompt
    view = overlay_group_member_memory(base, sender_id="a", profile=None)
    assert "identity_name" not in view["members"]["a"]
    assert "display_name_events" not in view["members"]["a"]
    assert base["members"]["a"]["identity_name"] == "LEGACY_IDENTITY"


@pytest.mark.parametrize("mode", ["raw", "transcribe"])
def test_forwarded_nodes_do_not_enrich_identity_from_unscoped_memory(mode):
    class Harness(ForwardMessageMixin, WorldbookMixin):
        enable_forward_message_adaptation = True
        enable_worldbook_member_recognition = True
        forward_message_mode = mode
        forward_message_provider_id = "synthetic-provider"
        mai_style_provider_id = ""
        forward_message_max_chars = 4000

    host = Harness()
    host.data = {
        "worldbook_member_profiles": {
            "123": {
                "user_id": "123",
                "observation_only": True,
                "identity_note": "LEGACY_GLOBAL_NOTE",
                "enabled": True,
            }
        }
    }
    host._find_forward_descriptor_for_event = AsyncMock(return_value=("forward-1", {}))
    host._extract_forward_messages_for_prompt = AsyncMock(
        return_value=(
            [
                {"sender_id": "123", "sender": "节点名字", "text": "原记录内容"},
                {
                    "sender_id": "bot",
                    "sender": "机器人",
                    "text": "原机器人内容",
                    "is_bot_self": True,
                },
            ],
            [],
            0,
        )
    )
    host._transcribe_forward_message_images = AsyncMock(return_value="")
    host._task_provider = lambda *_a: "synthetic-provider"
    host._llm_call = AsyncMock(return_value="合成转述")
    event = SimpleNamespace(message_str="看看记录")
    prompt = asyncio.run(
        host._format_forward_message_context_for_prompt(event, SimpleNamespace())
    )
    if mode == "transcribe":
        prompt = host._llm_call.call_args.args[0]
    assert "LEGACY_GLOBAL_NOTE" not in prompt
    assert "节点名字" in prompt
    assert "原记录内容" in prompt
    assert "Bot当时发出" in prompt


@pytest.mark.parametrize(
    "reason",
    [
        "human",
        "bot",
        "quoted",
        "forward",
        "injection",
        "disabled",
        "pending_off",
        "third_party",
    ],
)
def test_capture_only_collects_direct_human_self_reports(reason):
    group = {"members": {"10001": {"name": "群名片", "identity_name": "旧全局名字"}}}
    host = SimpleNamespace(
        _data_lock=asyncio.Lock(),
        data={},
        _get_group=lambda gid: group,
        _capture_group_observation_once=lambda *a, **kw: True,
        _event_self_id=lambda e: "20001",
        _event_message_id=lambda e: "msg1",
        _req041_group_sender_is_human=lambda e: reason != "bot",
        _worldbook_pending_observation_signal=lambda t: {
            "title": "偏好",
            "evidence": t,
        },
        _group_text_blocked_by_injection_guard=lambda *a, **kw: reason == "injection",
        _group_role_context_requested=lambda t: False,
        _schedule_data_save=lambda: None,
        enable_group_member_profiles=reason != "disabled",
        worldbook_auto_pending_observations=reason != "pending_off",
    )
    components = (
        [{"type": "reply" if reason == "quoted" else "forward", "data": {}}]
        if reason in {"quoted", "forward"}
        else []
    )
    event = SimpleNamespace(
        get_platform_name=lambda: "qq",
        get_sender_id=lambda: "10001",
        get_group_id=lambda: "g1",
        unified_msg_origin="default:GroupMessage:g1",
        get_messages=lambda: components,
    )
    text = "小明说他喜欢喝茶" if reason == "third_party" else "我平时喜欢喝茶"
    asyncio.run(
        PrivateCompanionPlugin._capture_group_observation_event(
            host,
            event,
            group_id="g1",
            sender_id="10001",
            sender_name="群名片",
            text=text,
            scene={"trigger": "normal"},
        )
    )
    scopes = list_scoped_member_scopes(host.data)
    if reason in {"bot", "disabled"}:
        assert scopes == []
    else:
        root = host.data["scoped_group_member_memories"]
        profile = next(iter(next(iter(root.values())).values()))
        assert "identity_name" not in profile
        assert bool(profile["pending_observations"]) == (reason == "human")
