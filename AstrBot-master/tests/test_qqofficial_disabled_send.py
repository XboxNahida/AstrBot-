import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_qqofficial_stream_buffer_copy import _make_group_event

from astrbot.api.event import MessageChain
from astrbot.api.platform import MessageType
from astrbot.core.platform.message_session import MessageSesion
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    QQOfficialPlatformAdapter,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "stream", "image", "media", "c2c"])
async def test_disabled_client_cannot_send_or_upload(mode):
    event = _make_group_event()
    event.bot._shutting_down = True
    event.bot.api._http = SimpleNamespace(
        request=AsyncMock(
            return_value={"file_uuid": "f", "file_info": "i", "id": "out"}
        )
    )

    async def stream():
        yield MessageChain().message("blocked")

    operations = {
        "normal": lambda: event.send(MessageChain().message("blocked")),
        "stream": lambda: event.send_streaming(stream()),
        "image": lambda: event.upload_group_and_c2c_image("eA==", 1, group_openid="g"),
        "media": lambda: event.upload_group_and_c2c_media(
            "https://example.invalid/a", 2, group_openid="g"
        ),
        "c2c": lambda: event.post_c2c_message("u", content="blocked"),
    }
    with pytest.raises(asyncio.CancelledError):
        await operations[mode]()
    event.bot.api.post_group_message.assert_not_awaited()
    event.bot.api._http.request.assert_not_awaited()
    assert not event._has_send_oper


@pytest.mark.asyncio
async def test_disable_during_media_preparation_blocks_text_and_success_marker():
    event = _make_group_event()

    async def prepare(_):
        event.bot._shutting_down = True
        return ("blocked", None, None, None, None, None, None)

    with patch(
        type(event).__module__ + ".QQOfficialMessageEvent._parse_to_qqofficial",
        side_effect=prepare,
    ):
        with pytest.raises(asyncio.CancelledError):
            await event.send(MessageChain().message("blocked"))
    event.bot.api.post_group_message.assert_not_awaited()
    assert not event._has_send_oper


@pytest.mark.asyncio
async def test_disable_after_first_failure_blocks_fallback():
    event = _make_group_event()
    from astrbot.core.platform.sources.qqofficial import (
        qqofficial_message_event as module,
    )

    class SendError(Exception):
        pass

    async def fail(**kwargs):
        event.bot._shutting_down = True
        raise SendError("first request failed")

    event.bot.api.post_group_message.side_effect = fail
    with patch.object(module, "_QQOFFICIAL_SEND_API_ERRORS", (SendError,)):
        with pytest.raises(asyncio.CancelledError):
            await event.send(MessageChain().message("blocked"))
    assert event.bot.api.post_group_message.await_count == 1
    assert not event._has_send_oper


@pytest.mark.asyncio
async def test_disable_during_retry_wait_blocks_next_upload(monkeypatch):
    event = _make_group_event()
    event.bot.api._http = SimpleNamespace(request=AsyncMock(return_value=None))

    async def stop_in_wait(_):
        event.bot._shutting_down = True

    monkeypatch.setattr(asyncio, "sleep", stop_in_wait)
    with pytest.raises(asyncio.CancelledError):
        await event.upload_group_and_c2c_image("eA==", 1, group_openid="g")
    assert event.bot.api._http.request.await_count == 1


@pytest.mark.asyncio
async def test_old_adapter_stays_disabled_when_new_instance_is_created():
    config = {
        "id": "test",
        "appid": "test",
        "secret": "unused",
        "enable_group_c2c": True,
        "enable_guild_direct_message": False,
    }
    old = QQOfficialPlatformAdapter(config, {}, asyncio.Queue())
    old.client.close = AsyncMock()
    await old.terminate()
    new = QQOfficialPlatformAdapter(config, {}, asyncio.Queue())
    for adapter in (old, new):
        adapter.client.api = SimpleNamespace(
            post_group_message=AsyncMock(return_value={"id": "sent"})
        )
        adapter.remember_session_scene("g", "group")
    session = MessageSesion(
        platform_name="test", message_type=MessageType.GROUP_MESSAGE, session_id="g"
    )
    with pytest.raises(asyncio.CancelledError):
        await old.send_by_session(session, MessageChain().message("blocked"))
    await new.send_by_session(session, MessageChain().message("allowed"))
    old.client.api.post_group_message.assert_not_awaited()
    new.client.api.post_group_message.assert_awaited_once()
