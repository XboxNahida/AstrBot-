from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlencode

from quart import Quart

from astrbot_plugin_private_companion.group_member_memory import (
    build_group_member_scope,
    read_scoped_member,
    record_group_member_observation,
    write_scoped_member,
)
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


def make_api():
    api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
    api.plugin = SimpleNamespace(data={}, _data_lock=asyncio.Lock(), saves=0)

    def save():
        api.plugin.saves += 1

    api.plugin._save_data_sync = save
    for bot, group in [("bot-a", "g1"), ("bot-b", "g1"), ("bot-a", "g2")]:
        scope = build_group_member_scope(bot, "qq", group)
        record_group_member_observation(
            api.plugin.data,
            scope,
            "qq:10001",
            {"name": bot},
            candidate={"evidence": "我平时喜欢喝茶", "source_event_id": bot + group},
            now=10,
        )
    return api


def request_api(api, handler, payload=None):
    app = Quart(__name__)

    async def run():
        if payload is None:
            async with app.test_request_context(
                "/list?"
                + urlencode(
                    {"bot_instance_id": "bot-a", "platform": "qq", "group_id": "g1"}
                )
            ):
                return await getattr(api, handler)()
        async with app.test_request_context("/update", method="POST", json=payload):
            return await getattr(api, handler)()

    return asyncio.run(run())


BASE = {
    "bot_instance_id": "bot-a",
    "platform": "qq",
    "group_id": "g1",
    "person_id": "qq:10001",
}


def test_list_requires_complete_scope_and_never_summarizes_other_groups():
    api = make_api()
    response = request_api(api, "list_group_member_memories")
    assert [m["name"] for m in response["data"]["members"]] == ["bot-a"]
    app = Quart(__name__)

    async def run():
        async with app.test_request_context("/list"):
            return await api.list_group_member_memories()

    assert asyncio.run(run())["success"] is False
    assert api.plugin.saves == 0


def test_review_preserves_evidence_and_other_scopes_then_edit_keeps_metadata():
    api = make_api()
    scope = build_group_member_scope("bot-a", "qq", "g1")
    other_scope = build_group_member_scope("bot-b", "qq", "g1")
    before_other = deepcopy(
        api.plugin.data["scoped_group_member_memories"][other_scope]
    )
    candidate = read_scoped_member(api.plugin.data, scope, "qq:10001")[
        "pending_observations"
    ][0]
    result = request_api(
        api,
        "update_group_member_memory",
        {**BASE, "accept_pending_observation_id": candidate["id"]},
    )
    memory = result["data"]["member"]["important_memories"][0]
    assert memory["source_event_id"] == "bot-ag1"
    assert memory["confidence"] == "confirmed"
    assert memory["created_at"] == 10
    assert memory["bot_instance_id"] == "bot-a"
    edited = {
        **memory,
        "content": "喜欢乌龙茶",
        "source_event_id": "forged",
        "bot_instance_id": "bot-b",
    }
    result = request_api(
        api, "update_group_member_memory", {**BASE, "important_memories": [edited]}
    )
    saved = result["data"]["member"]["important_memories"][0]
    assert saved["content"] == "喜欢乌龙茶"
    assert saved["source_event_id"] == "bot-ag1"
    assert saved["created_at"] == 10
    assert api.plugin.data["scoped_group_member_memories"][other_scope] == before_other


def test_wrong_scope_or_forged_profile_never_writes_and_delete_is_exact():
    api = make_api()
    before = deepcopy(api.plugin.data)
    result = request_api(
        api,
        "update_group_member_memory",
        {**BASE, "group_id": "unknown", "delete": True},
    )
    assert result["success"] is False
    assert api.plugin.data == before
    assert api.plugin.saves == 0
    scope = build_group_member_scope("bot-a", "qq", "g1")
    api.plugin.data["scoped_group_member_memories"][scope]["qq:10001"][
        "bot_instance_id"
    ] = "bot-b"
    assert (
        request_api(api, "update_group_member_memory", {**BASE, "delete": True})[
            "success"
        ]
        is False
    )
    write_scoped_member(
        api.plugin.data,
        scope,
        "qq:10001",
        before["scoped_group_member_memories"][scope]["qq:10001"],
    )
    assert (
        request_api(api, "update_group_member_memory", {**BASE, "enabled": False})[
            "success"
        ]
        is True
    )
    assert (
        request_api(api, "update_group_member_memory", {**BASE, "delete": True})[
            "success"
        ]
        is True
    )
    assert scope not in api.plugin.data["scoped_group_member_memories"]
    assert len(api.plugin.data["scoped_group_member_memories"]) == 2


def test_accept_reject_and_long_scope_cannot_mutate_another_target():
    api = make_api()
    before = deepcopy(api.plugin.data)
    other_scope = build_group_member_scope("bot-b", "qq", "g1")
    candidate = read_scoped_member(api.plugin.data, other_scope, "qq:10001")[
        "pending_observations"
    ][0]
    assert (
        request_api(
            api,
            "update_group_member_memory",
            {**BASE, "accept_pending_observation_id": candidate["id"]},
        )["success"]
        is False
    )
    assert api.plugin.data == before
    scope = build_group_member_scope("bot-a", "qq", "g1")
    candidate = read_scoped_member(api.plugin.data, scope, "qq:10001")[
        "pending_observations"
    ][0]
    result = request_api(
        api,
        "update_group_member_memory",
        {**BASE, "reject_pending_observation_id": candidate["id"]},
    )
    assert result["data"]["member"]["pending_observations"] == []
    assert result["data"]["member"]["important_memories"] == []
    long_bot = "a" * 160
    long_scope = build_group_member_scope(long_bot, "qq", "g1")
    write_scoped_member(api.plugin.data, long_scope, "qq:10001", {"name": "长键"})
    result = request_api(
        api,
        "update_group_member_memory",
        {**BASE, "bot_instance_id": long_bot + "collision", "delete": True},
    )
    assert result["success"] is False
    assert read_scoped_member(api.plugin.data, long_scope, "qq:10001") is not None
