"""Bounded short-lived interaction facts, independent of long-term affinity."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

EVENT_KINDS = frozenset(
    {
        "neutral",
        "disagree",
        "provoke",
        "tease",
        "apology",
        "comfort",
        "praise",
        "play",
        "topic_change",
    }
)
PHASES = frozenset({"steady", "flustered", "covering", "recovering"})


def advance_emotion(
    prior: Mapping[str, Any] | None,
    signal: Mapping[str, Any] | None,
    *,
    event_id: str,
    now: float,
) -> dict[str, Any]:
    """Settle one semantic event without changing any relationship account.

    Args:
        prior: State belonging to exactly one persona, session and sender.
        signal: Persona-adjusted semantic facts produced by the existing hub.
        event_id: Inbound message identity, reused during retries and tool loops.
        now: Current UNIX timestamp, supplied by the caller.

    Returns:
        A new JSON-compatible state with finite values and bounded history.
    """
    previous = dict(prior) if isinstance(prior, Mapping) else {}
    event = dict(signal) if isinstance(signal, Mapping) else {}
    ids = previous.get("event_ids", [])
    ids = [str(item)[:160] for item in ids[-64:]] if isinstance(ids, list) else []
    identity = str(event_id or "")[:160]
    if identity and identity in ids:
        return previous

    numbers = {}
    for key, default, low, high, source in (
        ("now", 0.0, 0.0, 1e12, {"now": now}),
        ("updated_at", 0.0, 0.0, 1e12, previous),
        ("recovery_seconds", 180.0, 30.0, 1800.0, previous),
        ("intensity", 0.0, 0.0, 1.0, event),
        ("confidence", 0.0, 0.0, 1.0, event),
        ("irritation", 0.0, 0.0, 1.0, previous),
        ("embarrassment", 0.0, 0.0, 1.0, previous),
        ("warmth", 0.0, 0.0, 1.0, previous),
    ):
        try:
            value = float(source.get(key, default))
        except (TypeError, ValueError, OverflowError):
            value = default
        numbers[key] = max(low, min(high, value if math.isfinite(value) else default))
    now = max(numbers["now"], numbers["updated_at"])
    elapsed = max(0.0, now - numbers["updated_at"])
    decay = 0.5 ** (elapsed / numbers["recovery_seconds"])
    state = {
        name: numbers[name] * decay
        for name in ("irritation", "embarrassment", "warmth")
    }
    state.update(
        {
            "phase": previous.get("phase", "steady") if decay > 0.125 else "steady",
            "reason": str(previous.get("reason") or "")[:160] if decay > 0.125 else "",
            "topic": str(previous.get("topic") or "")[:80] if decay > 0.125 else "",
            "recovery_seconds": numbers["recovery_seconds"],
        }
    )
    kind = str(event.get("kind") or "neutral")
    relevant = event.get("targets_bot") is True and numbers["confidence"] >= 0.65
    amount = numbers["intensity"]
    if relevant and kind in EVENT_KINDS:
        if kind == "provoke":
            state["irritation"] += amount * 0.45
        elif kind == "tease":
            state["embarrassment"] += amount * 0.5
        elif kind in {"apology", "comfort"}:
            state["irritation"] *= 1.0 - amount * 0.65
            state["embarrassment"] *= 1.0 - amount * 0.3
            state["warmth"] += amount * 0.2
        elif kind in {"praise", "play"}:
            state["warmth"] += amount * 0.3
        elif kind == "topic_change":
            state["irritation"] *= 0.45
            state["embarrassment"] *= 0.45
        if kind not in {"neutral", "disagree"}:
            state["reason"] = str(event.get("reason") or "")[:160]
            state["topic"] = str(event.get("topic") or "")[:80]
            phase = str(event.get("phase") or "steady")
            state["phase"] = phase if phase in PHASES else "steady"
            try:
                recovery = float(
                    event.get("recovery_seconds", numbers["recovery_seconds"])
                )
            except (TypeError, ValueError, OverflowError):
                recovery = numbers["recovery_seconds"]
            if math.isfinite(recovery):
                state["recovery_seconds"] = max(30.0, min(1800.0, recovery))
    for name in ("irritation", "embarrassment", "warmth"):
        state[name] = max(0.0, min(1.0, state[name]))
    state["updated_at"] = now
    state["event_ids"] = (ids + [identity])[-64:] if identity else ids
    return state
