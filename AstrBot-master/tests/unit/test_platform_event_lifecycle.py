import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.event_bus import EventBus
from astrbot.core.platform.platform import Platform
from astrbot.core.platform.platform_metadata import PlatformMetadata


class TestAdapter(Platform):
    __test__ = False

    async def run(self):
        await asyncio.Event().wait()

    def meta(self):
        return PlatformMetadata(name="test", description="test", id=self.config["id"])


def make_event():
    return SimpleNamespace(
        unified_msg_origin="test:GroupMessage:g",
        get_sender_name=lambda: "user",
        get_sender_id=lambda: "u",
        get_platform_id=lambda: "test",
        get_platform_name=lambda: "test",
        get_message_outline=lambda: "hello",
    )


@pytest.mark.asyncio
async def test_stopped_queued_event_does_not_reach_scheduler_after_reenable():
    queue = asyncio.Queue()
    old = TestAdapter({"id": "bot"}, queue)
    stale = make_event()
    old.commit_event(stale)
    old._accepting_events = False
    new = TestAdapter({"id": "bot"}, queue)
    fresh = make_event()
    new.commit_event(fresh)
    processed = asyncio.Event()

    async def execute(event):
        assert event is fresh
        processed.set()

    scheduler = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    bus = EventBus(
        queue, {"c": scheduler}, SimpleNamespace(get_conf_info=lambda _: {"id": "c"})
    )
    dispatcher = asyncio.create_task(bus.dispatch())
    try:
        await asyncio.wait_for(processed.wait(), 1)
        assert scheduler.execute.await_count == 1
    finally:
        dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await dispatcher


@pytest.mark.asyncio
async def test_pending_tasks_belong_to_original_instance_and_are_released():
    queue = asyncio.Queue()
    owner = TestAdapter({"id": "bot"}, queue)
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(event):
        from astrbot.core.platform.platform import CURRENT_EVENT_PLATFORM

        assert CURRENT_EVENT_PLATFORM.get() is owner
        started.set()
        await release.wait()

    bus = EventBus(
        queue,
        {"c": SimpleNamespace(execute=execute)},
        SimpleNamespace(get_conf_info=lambda _: {"id": "c"}),
    )
    owner.commit_event(make_event())
    dispatcher = asyncio.create_task(bus.dispatch())
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert owner._event_tasks == bus._pending_tasks
        assert len(owner._event_tasks) == 1
        tasks = tuple(owner._event_tasks)
        release.set()
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert not owner._event_tasks
        assert not bus._pending_tasks
    finally:
        dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await dispatcher


def test_stopped_instance_rejects_late_arrival():
    queue = asyncio.Queue()
    owner = TestAdapter({"id": "bot"}, queue)
    owner._accepting_events = False
    owner.commit_event(make_event())
    assert queue.empty()


@pytest.mark.asyncio
async def test_manager_cancels_only_stopped_platform_before_slow_connection_close():
    from astrbot.core.platform.manager import PlatformManager

    queue = asyncio.Queue()
    old = TestAdapter({"id": "old"}, queue)
    other = TestAdapter({"id": "other"}, queue)
    started = {old: asyncio.Event(), other: asyncio.Event()}
    cancelled = asyncio.Event()
    release = asyncio.Event()
    closing = asyncio.Event()

    async def execute(event):
        owner = event._platform_instance
        started[owner].set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            if owner is old:
                cancelled.set()
            raise

    async def terminate():
        closing.set()
        await release.wait()

    old.terminate = terminate
    manager = PlatformManager({"platform": [], "platform_settings": {}}, queue)
    manager.platform_insts = [old, other]
    manager._inst_map["old"] = {"client_id": old.client_self_id, "inst": old}
    bus = EventBus(
        queue,
        {"c": SimpleNamespace(execute=execute)},
        SimpleNamespace(get_conf_info=lambda _: {"id": "c"}),
    )
    old.commit_event(make_event())
    other.commit_event(make_event())
    dispatcher = asyncio.create_task(bus.dispatch())
    stopper = None
    try:
        await asyncio.wait_for(asyncio.gather(*(s.wait() for s in started.values())), 1)
        stopper = asyncio.create_task(manager.terminate_platform("old"))
        await asyncio.wait_for(closing.wait(), 1)
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not old._accepting_events
        assert other._accepting_events
        assert len(other._event_tasks) == 1
        assert not next(iter(other._event_tasks)).cancelling()
    finally:
        release.set()
        if stopper:
            await stopper
        await asyncio.gather(*tuple(bus._pending_tasks), return_exceptions=True)
        dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await dispatcher
