"""Scoped storage helpers for group member memories."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any

_STORE_KEY = "scoped_group_member_memories"
_SCOPE_PREFIX = "sgm:v1:"


def _part(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 160
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError(f"invalid {name}")
    return normalized


def build_group_member_scope(bot_instance_id: Any, platform: Any, group_id: Any) -> str:
    """Build an unambiguous key for one bot, platform, and group.

    Args:
        bot_instance_id: Stable identifier for the active bot instance.
        platform: Platform namespace for the group identifier.
        group_id: Platform group identifier.

    Returns:
        A versioned, deterministic scope key.

    Raises:
        ValueError: If a required identity component is missing or invalid.
    """
    parts = [
        _part(bot_instance_id, "bot_instance_id"),
        _part(platform, "platform"),
        _part(group_id, "group_id"),
    ]
    return _SCOPE_PREFIX + json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def group_member_event_identity(
    event: Any, bot_self_id: Any, group_id: Any
) -> tuple[str, str, str, str] | None:
    """Resolve the configured bot instance, platform, group, and sender identity.

    Args:
        event: Inbound group event.
        bot_self_id: Account ID fallback for adapters with stable self IDs.
        group_id: Exact platform group identifier.

    Returns:
        ``(scope, person_id, bot_instance_id, platform)`` or ``None`` when the
        event does not carry enough stable identity to isolate its memory.
    """
    try:
        platform = str(event.get_platform_name() or "").strip()
    except Exception:
        platform = ""
    try:
        actual_group = str(event.get_group_id() or "").strip()
    except (AttributeError, TypeError):
        origin = str(getattr(event, "unified_msg_origin", "") or "").split(":", 2)
        actual_group = (
            origin[2] if len(origin) == 3 and origin[1] == "GroupMessage" else ""
        )
    except Exception:
        return None
    if not actual_group or actual_group != str(group_id or "").strip():
        return None
    try:
        sender_id = str(event.get_sender_id() or "").strip()
    except Exception:
        sender_id = ""
    try:
        instance_id = str(event.get_platform_id() or "").strip()
    except AttributeError:
        instance_id = ""
    except Exception:
        return None
    self_id = str(bot_self_id or "").strip()
    if not platform or not sender_id:
        return None
    try:
        if instance_id:
            bot_instance_id = f"{platform}:instance:{_part(instance_id, 'instance_id')}"
        elif platform == "qq_official" or self_id.lower() in {
            "",
            "unknown",
            "unknown_selfid",
            "qq_official",
        }:
            # Official QQ self IDs can be placeholders or mention-dependent.
            return None
        else:
            bot_instance_id = f"{platform}:{_part(self_id, 'bot_self_id')}"
        person_id = f"{platform}:{_part(sender_id, 'sender_id')}"
        scope = build_group_member_scope(bot_instance_id, platform, group_id)
    except ValueError:
        return None
    return scope, person_id, bot_instance_id, platform


def _scope_parts(scope: Any) -> tuple[str, str, str]:
    value = str(scope or "")
    if not value.startswith(_SCOPE_PREFIX):
        raise ValueError("invalid group member scope")
    try:
        parts = json.loads(value[len(_SCOPE_PREFIX) :])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid group member scope") from exc
    if not isinstance(parts, list) or len(parts) != 3:
        raise ValueError("invalid group member scope")
    return tuple(_part(part, "scope") for part in parts)  # type: ignore[return-value]


def read_scoped_member(
    store: dict[str, Any], scope: Any, person_id: Any
) -> dict[str, Any] | None:
    """Read a member profile only from the exact scoped store key.

    Args:
        store: Plugin data dictionary.
        scope: Value returned by :func:`build_group_member_scope`.
        person_id: Stable identity for the group member.

    Returns:
        A detached profile copy, or ``None`` when no exact match exists.

    Raises:
        ValueError: If the requested scope or person identity is invalid.
    """
    bot_instance_id, platform, group_id = _scope_parts(scope)
    member_id = _part(person_id, "person_id")
    root = store.get(_STORE_KEY) if isinstance(store, dict) else None
    scoped = root.get(scope) if isinstance(root, dict) else None
    profile = scoped.get(member_id) if isinstance(scoped, dict) else None
    if not isinstance(profile, dict):
        return None
    if (
        profile.get("bot_instance_id") != bot_instance_id
        or profile.get("platform") != platform
        or profile.get("group_id") != group_id
        or profile.get("person_id") != member_id
    ):
        return None
    return deepcopy(profile)


def list_scoped_members(store: dict[str, Any], scope: Any) -> dict[str, dict[str, Any]]:
    """List valid member profiles from exactly one bot and group scope.

    Args:
        store: Plugin data dictionary.
        scope: Value returned by :func:`build_group_member_scope`.

    Returns:
        Detached profiles keyed by their person identity.

    Raises:
        ValueError: If the requested scope is invalid.
    """
    bot_instance_id, platform, group_id = _scope_parts(scope)
    root = store.get(_STORE_KEY) if isinstance(store, dict) else None
    scoped = root.get(scope) if isinstance(root, dict) else None
    if not isinstance(scoped, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_person_id, profile in scoped.items():
        try:
            person_id = _part(raw_person_id, "person_id")
        except ValueError:
            continue
        if not isinstance(profile, dict):
            continue
        if (
            profile.get("bot_instance_id") != bot_instance_id
            or profile.get("platform") != platform
            or profile.get("group_id") != group_id
            or profile.get("person_id") != person_id
        ):
            continue
        result[person_id] = deepcopy(profile)
    return result


def list_scoped_member_scopes(store: dict[str, Any]) -> list[dict[str, Any]]:
    """List nonempty, validated bot and group scopes for administration.

    Args:
        store: Plugin data dictionary.

    Returns:
        Scope descriptors with counts, sorted by platform, bot, and group.
    """
    root = store.get(_STORE_KEY) if isinstance(store, dict) else None
    if not isinstance(root, dict):
        return []
    result = []
    for scope in root:
        try:
            bot_instance_id, platform, group_id = _scope_parts(scope)
        except ValueError:
            continue
        members = list_scoped_members(store, scope)
        if members:
            result.append(
                {
                    "scope": scope,
                    "bot_instance_id": bot_instance_id,
                    "platform": platform,
                    "group_id": group_id,
                    "member_count": len(members),
                }
            )
    return sorted(
        result,
        key=lambda item: (item["platform"], item["bot_instance_id"], item["group_id"]),
    )


def write_scoped_member(
    store: dict[str, Any],
    scope: Any,
    person_id: Any,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Write a detached member profile under the exact scoped store key.

    Args:
        store: Plugin data dictionary.
        scope: Value returned by :func:`build_group_member_scope`.
        person_id: Stable identity for the group member.
        profile: Profile fields to save; scope fields are always server-owned.

    Returns:
        A detached copy of the saved profile.

    Raises:
        ValueError: If the store, scope, identity, or profile is invalid.
    """
    if not isinstance(store, dict) or not isinstance(profile, dict):
        raise ValueError("invalid scoped member storage input")
    bot_instance_id, platform, group_id = _scope_parts(scope)
    member_id = _part(person_id, "person_id")
    root = store.get(_STORE_KEY)
    if root is None:
        root = {}
        store[_STORE_KEY] = root
    if not isinstance(root, dict):
        raise ValueError("invalid scoped member store")
    scoped = root.get(scope)
    if scoped is None:
        scoped = {}
        root[scope] = scoped
    if not isinstance(scoped, dict):
        raise ValueError("invalid scoped member scope")
    saved = deepcopy(profile)
    saved.update(
        {
            "bot_instance_id": bot_instance_id,
            "platform": platform,
            "group_id": group_id,
            "person_id": member_id,
        }
    )
    scoped[member_id] = saved
    return deepcopy(saved)


def record_group_member_observation(
    store: dict[str, Any],
    scope: Any,
    person_id: Any,
    member: dict[str, Any],
    *,
    candidate: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Record an identity snapshot and an optional review-only candidate.

    Args:
        store: Plugin data dictionary.
        scope: Value returned by :func:`build_group_member_scope`.
        person_id: Stable identity for the group member.
        member: Current group member snapshot; message text fields are ignored.
        candidate: Filtered candidate requiring human review.
        now: Optional observation timestamp.

    Returns:
        The saved scoped member profile.
    """
    current = read_scoped_member(store, scope, person_id) or {
        "name": "",
        "group_role": "",
        "important_memories": [],
        "pending_observations": [],
        "enabled": True,
    }
    if isinstance(member, dict):
        display_name = str(member.get("name") or "").strip()[:80]
        if display_name:
            current["display_name"] = display_name
            if not current.get("name"):
                current["name"] = display_name
        for field in ("group_role", "group_role_label"):
            value = str(member.get(field) or "").strip()
            if value:
                current[field] = value[:80]
    if isinstance(candidate, dict) and current.get("enabled", True):
        evidence = str(candidate.get("evidence") or "").strip()[:160]
        title = str(candidate.get("title") or "群聊观察").strip()[:60]
        if evidence:
            pending = current.get("pending_observations")
            if not isinstance(pending, list):
                pending = []
            existing = next(
                (
                    item
                    for item in pending
                    if isinstance(item, dict) and item.get("evidence") == evidence
                ),
                None,
            )
            timestamp = float(now if now is not None else time.time())
            if existing is not None:
                existing["count"] = min(999, int(existing.get("count") or 1) + 1)
                existing["updated_at"] = timestamp
            else:
                bot_instance_id, platform, group_id = _scope_parts(scope)
                candidate_id = hashlib.sha256(
                    f"{scope}:{person_id}:{evidence}".encode()
                ).hexdigest()[:24]
                pending.insert(
                    0,
                    {
                        "id": candidate_id,
                        "title": title,
                        "evidence": evidence,
                        "count": 1,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                        "status": "pending",
                        "source_event_id": str(candidate.get("source_event_id") or "")[
                            :160
                        ],
                        "evidence_type": "self_report",
                        "confidence": "unconfirmed",
                        "bot_instance_id": bot_instance_id,
                        "platform": platform,
                        "group_id": group_id,
                        "person_id": _part(person_id, "person_id"),
                    },
                )
            current["pending_observations"] = pending[:24]
    current["last_seen"] = float(now if now is not None else time.time())
    return write_scoped_member(store, scope, person_id, current)


__all__ = [
    "build_group_member_scope",
    "group_member_event_identity",
    "list_scoped_members",
    "list_scoped_member_scopes",
    "read_scoped_member",
    "record_group_member_observation",
    "write_scoped_member",
]
