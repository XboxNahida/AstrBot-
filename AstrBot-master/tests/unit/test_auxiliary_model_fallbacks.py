"""Exercise auxiliary model failover without contacting upstream APIs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core import astr_main_agent as ama
from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.agent.message import Message
from astrbot.core.provider.provider import Provider
from data.plugins.astrbot_plugin_image_caption_cache.cache import ImageCaptionCache
from data.plugins.astrbot_plugin_image_caption_cache.patcher import ImageCaptionCachePatcher


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("failure", [TimeoutError("offline timeout"), ""])
async def test_image_caption_uses_configured_image_fallback(cached, failure):
    primary = MagicMock(spec=Provider)
    primary.provider_config = {"id": "gemini", "modalities": ["text", "image"]}
    primary.text_chat = AsyncMock(
        side_effect=failure if isinstance(failure, Exception) else None,
        return_value=SimpleNamespace(completion_text=""),
    )
    fallback = MagicMock(spec=Provider)
    fallback.provider_config = {"id": "deepseek", "modalities": ["text", "image"]}
    fallback.text_chat = AsyncMock(return_value=SimpleNamespace(completion_text="image facts"))
    context = MagicMock()
    context.get_provider_by_id.side_effect = {"gemini": primary, "deepseek": fallback}.get
    cfg = {"fallback_chat_models": ["gemini", "missing", "deepseek", "deepseek"], "request_max_retries": 3}
    patcher = ImageCaptionCachePatcher(cache=ImageCaptionCache(), ttl_resolver=lambda _: 600, logger=MagicMock())
    if cached:
        patcher.apply(patch_quoted_message=False, force_caption_for_vision_models=False)
    try:
        assert await ama._request_img_caption("gemini", cfg, ["https://example.test/a.png"], context) == "image facts"
        if cached:
            assert await ama._request_img_caption("gemini", cfg, ["https://example.test/a.png"], context) == "image facts"
    finally:
        patcher.restore()
    primary.text_chat.assert_awaited_once()
    fallback.text_chat.assert_awaited_once()
    assert fallback.text_chat.await_args.kwargs["request_max_retries"] == 3
    assert fallback.text_chat.await_args.kwargs["image_urls"] == ["https://example.test/a.png"]


@pytest.mark.asyncio
async def test_cached_quoted_image_uses_session_fallback_settings(monkeypatch):
    primary = MagicMock(spec=Provider)
    primary.provider_config = {"id": "gemini", "modalities": ["text", "image"]}
    primary.text_chat = AsyncMock(side_effect=TimeoutError("offline"))
    backup = MagicMock(spec=Provider)
    backup.provider_config = {"id": "deepseek", "modalities": ["text", "image"]}
    backup.text_chat = AsyncMock(return_value=SimpleNamespace(completion_text="quoted facts"))
    context = MagicMock()
    context.get_provider_by_id.side_effect = {"gemini": primary, "deepseek": backup}.get
    context.get_config.return_value = {"provider_settings": {"fallback_chat_models": ["deepseek"], "request_max_retries": 3}}
    image = SimpleNamespace(convert_to_file_path=AsyncMock(return_value="https://example.test/quote.png"))
    monkeypatch.setattr(ama, "_compress_image_for_provider", AsyncMock(side_effect=lambda path, _: path))
    patcher = ImageCaptionCachePatcher(cache=ImageCaptionCache(), ttl_resolver=lambda _: 600, logger=MagicMock())
    parts = []
    await patcher._append_cached_quoted_image_caption(ama, SimpleNamespace(unified_msg_origin="group:test"), parts, image, "gemini", context, None)
    assert parts == ["[Image Caption in quoted message]: quoted facts"]
    assert backup.text_chat.await_args.kwargs["request_max_retries"] == 3


@pytest.mark.asyncio
async def test_summary_keeps_original_history_when_both_providers_fail():
    providers = []
    for name in ("gemini", "deepseek"):
        provider = MagicMock(spec=Provider)
        provider.provider_config = {"id": name, "modalities": ["text"]}
        provider.text_chat = AsyncMock(side_effect=TimeoutError("offline"))
        providers.append(provider)
    manager = ContextManager(ContextConfig(llm_compress_provider=providers[0], llm_compress_fallback_providers=[providers[1]], request_max_retries=3))
    messages = [Message(role="user", content="question"), Message(role="assistant", content="answer")]
    assert await manager.compressor(messages) is messages
    for provider in providers:
        provider.text_chat.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("offline timeout"), ""])
async def test_summary_falls_back_and_preserves_active_request(failure):
    primary = MagicMock(spec=Provider)
    primary.provider_config = {"id": "gemini", "modalities": ["text", "image"]}
    primary.text_chat = AsyncMock(
        side_effect=failure if isinstance(failure, Exception) else None,
        return_value=SimpleNamespace(completion_text=""),
    )
    fallback = MagicMock(spec=Provider)
    fallback.provider_config = {"id": "deepseek", "modalities": ["text"]}
    fallback.text_chat = AsyncMock(return_value=SimpleNamespace(completion_text="history facts"))
    manager = ContextManager(ContextConfig(
        llm_compress_provider=primary,
        llm_compress_fallback_providers=[fallback],
        request_max_retries=3,
    ))
    messages = [Message(role="user", content="old question"), Message(role="assistant", content="old answer"), Message(role="user", content="active request")]
    result = await manager.compressor(messages)
    assert "history facts" in result[0].content
    assert result[-1] is messages[-1]
    assert len(messages) == 3
    assert fallback.text_chat.await_args.kwargs["request_max_retries"] == 3

