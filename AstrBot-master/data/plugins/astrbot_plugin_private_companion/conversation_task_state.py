"""Bounded, request-independent state for background conversation tasks.

The store deliberately keeps only a small safe summary.  It is used to make a
failed or long-running GPT task visible to the next Gemini turn without
persisting secrets or an unbounded transcript.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, fields
from typing import Any


TASK_STATUSES = frozenset({"queued", "running", "progress", "complete", "failed", "timeout"})


@dataclass(slots=True)
class ConversationTaskState:
    task_id: str
    session_key: str
    task_text: str
    unfinished_task: str
    provider_id: str
    fallback_provider_id: str
    status: str
    started_at: float
    updated_at: float
    estimated_seconds: int
    progress_note: str = ""
    error: str = ""
    result_preview: str = ""
    sender_id: str = ""
    conversation_id: str = ""
    phase: str = "waiting_model"
    revision: int = 0
    tool_name: str = ""
    tool_result: str = ""
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    reply_context: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        """Return a bounded, JSON-safe view suitable for prompt injection."""
        data = {item.name: getattr(self, item.name) for item in fields(self) if item.name != "reply_context"}
        data["task_text"] = data["task_text"][:2400]
        data["unfinished_task"] = data["unfinished_task"][:2400]
        data["progress_note"] = data["progress_note"][:500]
        data["error"] = data["error"][:800]
        data["result_preview"] = data["result_preview"][:1600]
        data["tool_events"] = [dict(item) for item in self.tool_events[-4:]]
        data["notices"] = list(self.notices[-4:])
        return data


class ConversationTaskStateStore:
    """Keep the newest task states with deterministic lifecycle transitions."""

    def __init__(self, *, max_tasks: int = 32, clock: Any = time.time) -> None:
        self.max_tasks = max(1, int(max_tasks))
        self._clock = clock
        self._tasks: OrderedDict[str, ConversationTaskState] = OrderedDict()
        self._notice_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    def _touch(self, state: ConversationTaskState) -> ConversationTaskState:
        state.updated_at = float(self._clock())
        state.revision += 1
        self._tasks.move_to_end(state.task_id)
        return state

    def _find(self, task_id: str) -> ConversationTaskState:
        state = self._tasks.get(str(task_id or ""))
        if state is None:
            raise KeyError(f"unknown conversation task: {task_id}")
        return state

    def start_task(
        self,
        *,
        session_key: str,
        task_text: str,
        provider_id: str,
        fallback_provider_id: str = "",
        estimated_seconds: int = 0,
        task_id: str | None = None,
        sender_id: str = "",
        conversation_id: str = "",
    ) -> ConversationTaskState:
        now = float(self._clock())
        estimate = max(0, min(3600, int(estimated_seconds or 0)))
        state = ConversationTaskState(
            task_id=self._text(task_id, 120) or uuid.uuid4().hex,
            session_key=self._text(session_key, 240),
            task_text=self._text(task_text, 2400),
            unfinished_task=self._text(task_text, 2400),
            provider_id=self._text(provider_id, 240),
            fallback_provider_id=self._text(fallback_provider_id, 240),
            status="running",
            started_at=now,
            updated_at=now,
            estimated_seconds=estimate,
            sender_id=self._text(sender_id, 160),
            conversation_id=self._text(conversation_id, 160),
        )
        self._tasks[state.task_id] = state
        while len(self._tasks) > self.max_tasks:
            evicted, _ = self._tasks.popitem(last=False)
            self._notice_locks.pop(evicted, None)
        return state

    def get(self, task_id: str | None) -> ConversationTaskState | None:
        return self._tasks.get(str(task_id or ""))

    def mark_progress(self, task_id: str, progress_note: str) -> ConversationTaskState:
        state = self._find(task_id)
        if state.status in {"complete", "failed", "timeout"}:
            return state
        state.status = "progress"
        state.progress_note = self._text(progress_note, 500)
        return self._touch(state)

    def notice_lock(self, task_id: str) -> asyncio.Lock:
        """Serialize notices for one task across automatic and query events.

        Args:
            task_id: Existing task identifier.

        Returns:
            A bounded, task-scoped asyncio lock.
        """
        self._find(task_id)
        return self._notice_locks.setdefault(task_id, asyncio.Lock())

    def record_tool_start(self, task_id: str, name: str) -> ConversationTaskState:
        """Record a real tool invocation without claiming its success.

        Args:
            task_id: Task receiving the invocation.
            name: Tool name observed by the framework hook.

        Returns:
            Updated task, or an unchanged terminal task.
        """
        state = self._find(task_id)
        if state.status in {"complete", "failed", "timeout"}:
            return state
        state.status = "progress"
        state.phase = "tool_running"
        state.tool_name = self._text(name, 120)
        state.progress_note = "工具调用已开始，尚未返回结果"
        return self._touch(state)

    def record_tool_result(self, task_id: str, name: str, result: str, *, failed: bool = False, background_running: bool = False) -> ConversationTaskState:
        """Keep bounded tool evidence while allowing the agent to recover.

        Args:
            task_id: Task receiving the result.
            name: Observed tool name.
            result: Redacted, bounded result text.
            failed: A structured tool error or nonzero process exit.
            background_running: A managed process explicitly remains running.

        Returns:
            Updated task, or an unchanged terminal task.
        """
        state = self._find(task_id)
        if state.status in {"complete", "failed", "timeout"}:
            return state
        state.status = "progress"
        state.phase = "tool_error" if failed else ("background_running" if background_running else "waiting_model")
        state.tool_name = self._text(name, 120)
        state.tool_result = self._text(result, 1200)
        state.progress_note = "工具返回错误，任务尚未结束" if failed else ("后台进程仍在运行" if background_running else "已收到工具结果，等待任务模型继续处理")
        state.tool_events = (state.tool_events + [{"tool": state.tool_name, "result": state.tool_result[:500], "failed": bool(failed)}])[-4:]
        return self._touch(state)

    def complete_task(self, task_id: str, result: str) -> ConversationTaskState:
        state = self._find(task_id)
        if state.status in {"failed", "timeout"}:
            return state
        state.status = "complete"
        state.phase = "finished"
        state.result_preview = self._text(result, 1600)
        state.progress_note = ""
        state.unfinished_task = ""
        return self._touch(state)

    def fail_task(self, task_id: str, error: str, *, unfinished_task: str | None = None) -> ConversationTaskState:
        state = self._find(task_id)
        if state.status == "complete":
            return state
        state.status = "failed"
        state.phase = "failed"
        state.error = self._text(error, 800)
        if unfinished_task is not None:
            state.unfinished_task = self._text(unfinished_task, 2400)
        return self._touch(state)

    def timeout_task(self, task_id: str, error: str = "") -> ConversationTaskState:
        state = self._find(task_id)
        if state.status in {"complete", "failed", "timeout"}:
            return state
        state.status = "timeout"
        state.phase = "timeout"
        state.error = self._text(error, 800)
        return self._touch(state)

    def get_active_task(self, session_key: str, *, sender_id: str = "", conversation_id: str | None = None) -> ConversationTaskState | None:
        key = self._text(session_key, 240)
        for state in reversed(tuple(self._tasks.values())):
            if (state.session_key == key and state.status in {"queued", "running", "progress"}
                    and (not sender_id or state.sender_id == sender_id)
                    and (conversation_id is None or state.conversation_id == conversation_id)):
                return state
        return None

    def summary_for_session(self, session_key: str, *, sender_id: str = "", conversation_id: str | None = None) -> dict[str, Any]:
        key = self._text(session_key, 240)
        for state in reversed(tuple(self._tasks.values())):
            if (state.session_key == key and (not sender_id or state.sender_id == sender_id)
                    and (conversation_id is None or state.conversation_id == conversation_id)):
                return state.summary()
        return {}
