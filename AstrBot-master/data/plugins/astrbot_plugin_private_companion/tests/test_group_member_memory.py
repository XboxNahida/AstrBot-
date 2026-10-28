from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.constants import _DATA_STORE_KEYS
from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.group_member_memory import (
    build_group_member_scope,
    group_member_event_identity,
    list_scoped_member_scopes,
    list_scoped_members,
    read_scoped_member,
    record_group_member_observation,
    write_scoped_member,
)


class GroupMemberMemoryScopeTests(unittest.TestCase):
    def test_official_bot_instance_is_stable_across_mentions_and_isolated(self) -> None:
        event = SimpleNamespace(
            get_platform_name=lambda: "qq_official",
            get_platform_id=lambda: "Nahida",
            get_sender_id=lambda: "member-openid",
            get_group_id=lambda: "group-openid",
        )
        ordinary = group_member_event_identity(event, "qq_official", "group-openid")
        mentioned = group_member_event_identity(
            event, "real-bot-openid", "group-openid"
        )
        self.assertEqual(ordinary, mentioned)
        self.assertIsNotNone(ordinary)
        event.get_platform_id = lambda: "likh"
        other = group_member_event_identity(event, "qq_official", "group-openid")
        self.assertNotEqual(ordinary[0], other[0])
        event.get_platform_id = lambda: ""
        self.assertIsNone(
            group_member_event_identity(event, "qq_official", "group-openid")
        )
        self.assertIsNone(
            group_member_event_identity(event, "real-bot-openid", "group-openid")
        )

    def test_adapter_instance_does_not_require_synthetic_self_id(self) -> None:
        event = SimpleNamespace(
            get_platform_name=lambda: "qq_official",
            get_platform_id=lambda: "Nahida",
            get_sender_id=lambda: "member-openid",
            get_group_id=lambda: "group-openid",
        )
        self.assertIsNotNone(group_member_event_identity(event, "", "group-openid"))

    def test_observation_keeps_edited_name_and_updates_display_snapshot(self) -> None:
        data = {}
        scope = build_group_member_scope("bot-a", "qq", "g1")
        write_scoped_member(
            data,
            scope,
            "qq:10001",
            {
                "name": "手动名称",
                "preferred_address": "手动称呼",
            },
        )
        saved = record_group_member_observation(
            data, scope, "qq:10001", {"name": "最新群名片"}, now=10
        )
        self.assertEqual("手动名称", saved["name"])
        self.assertEqual("手动称呼", saved["preferred_address"])
        self.assertEqual("最新群名片", saved["display_name"])

    def test_event_identity_rejects_wrong_group_and_unknown_platform(self) -> None:
        event = SimpleNamespace(
            get_platform_name=lambda: "qq",
            get_sender_id=lambda: "10001",
            get_group_id=lambda: "g1",
        )
        self.assertIsNone(group_member_event_identity(event, "20001", "g2"))
        event.get_platform_name = lambda: ""
        event.unified_msg_origin = "default:GroupMessage:g1"
        self.assertIsNone(group_member_event_identity(event, "20001", "g1"))

    def test_candidates_keep_scope_and_source_without_promoting_disabled_profile(
        self,
    ) -> None:
        data = {}
        scope = build_group_member_scope("qq:20001", "qq", "g1")
        candidate = {
            "title": "偏好",
            "evidence": "我平时喜欢喝茶",
            "source_event_id": "msg1",
        }
        saved = record_group_member_observation(
            data, scope, "qq:10001", {}, candidate=candidate, now=10
        )
        pending = saved["pending_observations"][0]
        self.assertEqual("msg1", pending["source_event_id"])
        self.assertEqual("qq:20001", pending["bot_instance_id"])
        self.assertEqual("self_report", pending["evidence_type"])
        self.assertEqual("pending", pending["status"])
        self.assertEqual([], saved["important_memories"])
        saved["enabled"] = False
        write_scoped_member(data, scope, "qq:10001", saved)
        record_group_member_observation(
            data, scope, "qq:10001", {}, candidate={"evidence": "我喜欢咖啡"}
        )
        self.assertEqual(
            1, len(read_scoped_member(data, scope, "qq:10001")["pending_observations"])
        )

    def test_event_identity_requires_exact_self_and_sender_ids(self) -> None:
        event = type(
            "Event",
            (),
            {
                "get_platform_name": lambda self: "aiocqhttp",
                "get_sender_id": lambda self: "10001",
                "unified_msg_origin": "aiocqhttp:GroupMessage:group-1",
            },
        )()

        identity = group_member_event_identity(event, "20001", "group-1")

        self.assertEqual("aiocqhttp:20001", identity[2])
        self.assertEqual("aiocqhttp:10001", identity[1])
        self.assertIsNone(group_member_event_identity(event, "", "group-1"))
        self.assertIsNone(group_member_event_identity(event, "20001", ""))

    def test_scoped_store_is_registered_and_defaulted_without_migration(self) -> None:
        legacy = {"10001": {"name": "old-profile"}}
        group_topics = {"g1": {"group_episodes": [{"summary": "shared"}]}}
        data = {"worldbook_member_profiles": legacy, "groups": group_topics}

        CoreStoreMixin._ensure_store_defaults(data)

        self.assertIn("scoped_group_member_memories", _DATA_STORE_KEYS)
        self.assertEqual({}, data["scoped_group_member_memories"])
        self.assertIs(legacy, data["worldbook_member_profiles"])
        self.assertIs(group_topics, data["groups"])

    def test_scope_isolated_by_bot_platform_group_and_person(self) -> None:
        base = build_group_member_scope("bot-a", "qq", "group-1")
        variants = (
            build_group_member_scope("bot-b", "qq", "group-1"),
            build_group_member_scope("bot-a", "wx", "group-1"),
            build_group_member_scope("bot-a", "qq", "group-2"),
        )

        self.assertTrue(all(scope != base for scope in variants))
        self.assertEqual(len(variants) + 1, len({base, *variants}))
        self.assertNotEqual(
            build_group_member_scope("bot-a|qq", "group-1", "platform"),
            build_group_member_scope("bot-a", "qq|group-1", "platform"),
        )

    def test_scope_rejects_missing_or_ambiguous_identity_parts(self) -> None:
        for parts in (
            ("", "qq", "group-1"),
            ("bot-a", "", "group-1"),
            ("bot-a", "qq", ""),
        ):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                build_group_member_scope(*parts)

    def test_write_and_read_do_not_touch_legacy_profiles(self) -> None:
        legacy_profile = {"10001": {"name": "旧档案", "important_memories": []}}
        data = {
            "worldbook_member_profiles": legacy_profile,
            "groups": {"group-1": {"group_episodes": [{"summary": "共享旧话题"}]}},
            "scoped_group_member_memories": {},
        }
        scope = build_group_member_scope("bot-a", "qq", "group-1")

        saved = write_scoped_member(
            data,
            scope,
            "person-1",
            {"name": "小明", "scope_id": "forged", "person_id": "forged"},
        )

        self.assertEqual("bot-a", saved["bot_instance_id"])
        self.assertEqual("group-1", saved["group_id"])
        self.assertEqual("person-1", saved["person_id"])
        self.assertEqual(
            {"name": "旧档案", "important_memories": []},
            data["worldbook_member_profiles"]["10001"],
        )
        self.assertEqual(
            "共享旧话题", data["groups"]["group-1"]["group_episodes"][0]["summary"]
        )
        self.assertEqual("小明", read_scoped_member(data, scope, "person-1")["name"])
        self.assertIsNone(read_scoped_member(data, scope, "person-2"))
        with self.assertRaises(ValueError):
            read_scoped_member(data, "other-scope", "person-1")

    def test_legacy_personal_profile_is_never_a_scoped_read_fallback(self) -> None:
        data = {
            "worldbook_member_profiles": {
                "qq:10001": {"name": "旧档案", "important_memories": ["旧记忆"]}
            },
            "scoped_group_member_memories": {},
        }
        scope = build_group_member_scope("qq:20001", "qq", "group-1")

        self.assertIsNone(read_scoped_member(data, scope, "qq:10001"))
        self.assertEqual(
            "旧档案", data["worldbook_member_profiles"]["qq:10001"]["name"]
        )

    def test_scoped_member_listing_stays_inside_exact_scope(self) -> None:
        data = {"scoped_group_member_memories": {}}
        scope = build_group_member_scope("qq:20001", "qq", "group-1")
        other_scope = build_group_member_scope("qq:20001", "qq", "group-2")
        write_scoped_member(data, scope, "qq:10001", {"name": "同群成员"})
        write_scoped_member(data, other_scope, "qq:10002", {"name": "别群成员"})

        listed = list_scoped_members(data, scope)

        self.assertEqual(["qq:10001"], sorted(listed))
        self.assertEqual("同群成员", listed["qq:10001"]["name"])

    def test_scope_index_lists_only_nonempty_valid_scopes(self) -> None:
        data = {"scoped_group_member_memories": {}}
        scope = build_group_member_scope("qq:20001", "qq", "group-1")
        write_scoped_member(data, scope, "qq:10001", {"name": "成员"})

        scopes = list_scoped_member_scopes(data)

        self.assertEqual(
            [
                {
                    "scope": scope,
                    "bot_instance_id": "qq:20001",
                    "platform": "qq",
                    "group_id": "group-1",
                    "member_count": 1,
                }
            ],
            scopes,
        )

    def test_observation_stores_only_scoped_identity_and_review_candidates(
        self,
    ) -> None:
        data: dict = {
            "worldbook_member_profiles": {"10001": {"name": "legacy"}},
            "groups": {"g1": {"group_episodes": [{"summary": "shared"}]}},
        }
        scope = build_group_member_scope("bot-a", "qq", "g1")
        member = {
            "name": "小明",
            "group_role": "member",
            "recent_phrases": ["raw chat must not be copied"],
            "recent_messages": [{"text": "raw transcript must not be copied"}],
        }
        candidate = {"title": "偏好", "evidence": "我平时喜欢喝茶"}

        first = record_group_member_observation(
            data, scope, "qq:10001", member, candidate=candidate, now=10
        )
        second = record_group_member_observation(
            data, scope, "qq:10001", member, candidate=candidate, now=20
        )

        self.assertEqual("小明", first["name"])
        self.assertEqual(2, second["pending_observations"][0]["count"])
        self.assertNotIn("recent_phrases", second)
        self.assertNotIn("recent_messages", second)
        self.assertEqual("legacy", data["worldbook_member_profiles"]["10001"]["name"])
        self.assertEqual("shared", data["groups"]["g1"]["group_episodes"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
