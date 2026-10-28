import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot.core import astr_main_agent as agent
from astrbot.core.provider import Provider
from astrbot.core.provider.entities import ProviderRequest


def provider(identity, *, image=False):
    result = MagicMock(spec=Provider)
    result.provider_config = {
        "id": identity,
        "modalities": ["text", "image"] if image else ["text"],
    }
    result.get_model.return_value = identity
    return result


class RequestProviderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.gpt = provider("gpt")
        self.gemini = provider("gemini", image=True)
        self.grok = provider("grok")
        self.providers = {"gpt": self.gpt, "gemini": self.gemini, "grok": self.grok}
        self.context = SimpleNamespace(get_provider_by_id=self.providers.get)
        self.settings = {"fallback_chat_models": ["gpt"]}

    def test_explicit_chat_fallback_does_not_inherit_global_gpt(self):
        req = ProviderRequest(provider_id="gemini", fallback_provider_ids=["grok"])
        primary, backups = agent.resolve_request_provider_policy(
            self.gpt, req, self.context, self.settings
        )
        self.assertIs(primary, self.gemini)
        self.assertEqual([self.grok], backups)
        self.assertIsNone(req.model)

    def test_empty_fallback_disables_global_fallback_and_legacy_none_keeps_it(self):
        req = ProviderRequest(provider_id="grok", fallback_provider_ids=[])
        primary, backups = agent.resolve_request_provider_policy(
            self.gpt, req, self.context, self.settings
        )
        self.assertIs(primary, self.grok)
        self.assertEqual([], backups)
        legacy = ProviderRequest()
        primary, backups = agent.resolve_request_provider_policy(
            self.grok, legacy, self.context, self.settings
        )
        self.assertEqual([self.gpt], backups)

    def test_missing_or_wrong_provider_is_rejected_without_using_default(self):
        for identity in ("missing", "not-chat"):
            self.providers["not-chat"] = object()
            with self.assertRaises(ValueError):
                agent.resolve_request_provider_policy(
                    self.gpt,
                    ProviderRequest(provider_id=identity, fallback_provider_ids=[]),
                    self.context,
                    self.settings,
                )

    def test_image_selection_stays_inside_explicit_whitelist(self):
        req = ProviderRequest(
            provider_id="grok", fallback_provider_ids=["gemini"], image_urls=["image"]
        )
        primary, backups = agent.resolve_request_provider_policy(
            self.gpt, req, self.context, self.settings
        )
        self.assertIs(primary, self.gemini)
        self.assertNotIn(self.gpt, backups)

    def test_malformed_fallback_list_cannot_enable_global_fallback(self):
        req = ProviderRequest(provider_id="grok", fallback_provider_ids="gpt")
        with self.assertRaises(ValueError):
            agent.resolve_request_provider_policy(
                self.gpt, req, self.context, self.settings
            )

    def test_disabled_image_backup_is_never_selected(self):
        self.gemini.provider_config["enable"] = False
        req = ProviderRequest(
            provider_id="grok", fallback_provider_ids=["gemini"], image_urls=["image"]
        )
        with self.assertRaises(ValueError):
            agent.resolve_request_provider_policy(
                self.gpt, req, self.context, self.settings
            )


class InternalRequestRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_hook_override_reaches_actual_runner_and_can_disable_streaming(self):
        from astrbot.core.pipeline.process_stage.method.agent_sub_stages import internal
        from astrbot.core.star.star_handler import EventType

        gpt, gemini = provider("gpt"), provider("gemini")
        context = SimpleNamespace(get_provider_by_id={"gpt": gpt, "gemini": gemini}.get)
        req = ProviderRequest(prompt="hello")
        runner = MagicMock()
        runner.reset = AsyncMock(side_effect=RuntimeError("test reset probe"))
        original = AsyncMock()()
        built = agent.MainAgentBuildResult(
            agent_runner=runner,
            provider_request=req,
            provider=gpt,
            reset_coro=original,
            reset_kwargs={"request": req, "provider": gpt, "streaming": True},
        )
        stage = internal.InternalAgentSubStage()
        stage.ctx = SimpleNamespace(plugin_manager=SimpleNamespace(context=context))
        stage.main_agent_cfg = agent.MainAgentBuildConfig(
            tool_call_timeout=60, provider_settings={"fallback_chat_models": ["gpt"]}
        )
        stage.streaming_response = True
        stage.unsupported_streaming_strategy = "turn_off"
        event = MagicMock()
        event.get_extra.return_value = None
        event.message_str = "hello"
        event.message_obj.message = []
        event.unified_msg_origin = "test:private:u1"
        event.send_typing = AsyncMock()
        event.stop_typing = AsyncMock()
        event.send = AsyncMock()

        async def hook(_event, kind, *args):
            if kind == EventType.OnLLMRequestEvent:
                req.provider_id = "gemini"
                req.fallback_provider_ids = []
                req.disable_streaming = True
            return False

        with (
            patch.object(internal, "build_main_agent", AsyncMock(return_value=built)),
            patch.object(internal, "try_capture_follow_up", return_value=None),
            patch.object(internal, "call_event_hook", hook),
        ):
            async for _ in stage.process(event, ""):
                pass
        runner.reset.assert_awaited_once()
        self.assertIs(gemini, runner.reset.await_args.kwargs["provider"])
        self.assertEqual([], runner.reset.await_args.kwargs["fallback_providers"])
        self.assertFalse(runner.reset.await_args.kwargs["streaming"])
        self.assertIsNone(original.cr_frame)
