import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot_plugin_private_companion import private_image as image_module
from astrbot_plugin_private_companion.private_image import PrivateImageMixin
from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin


class RequestCaptured(BaseException):
    pass


class ImageTurnBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def make_harness(self, *, direct=False):
        harness = PrivateImageMixin()
        harness.context = SimpleNamespace(get_config=lambda **kw: {})
        for name, value in {
            "_private_image_ownership_line": "",
            "_private_image_intent_line": "",
            "_private_image_reply_objective": "",
            "_private_image_vision_text_limit": 1400,
            "_private_image_model_image_items": [],
            "_private_image_sources_for_astrbot_request": ["image.png"],
            "_private_image_framework_context": None,
            "_event_main_provider_supports_image": direct,
            "_has_private_image_visual_provider": not direct,
            "_private_image_sources_include_gif": False,
            "_private_image_delivery_mode": "direct" if direct else "caption",
            "_route_private_image_caption_with_keyword_router": False,
            "_format_recent_group_messages_for_private_image_prompt": "",
        }.items():
            setattr(harness, name, MagicMock(return_value=value))
        harness._transcribe_private_inbound_images = AsyncMock(
            return_value="一张写着“就这？”的猫咪表情包"
        )
        harness.inject_humanized_state = AsyncMock(side_effect=RequestCaptured)
        event = SimpleNamespace(
            message_str="[图片]", unified_msg_origin="", get_extra=lambda key: None
        )
        return (
            harness,
            event,
            {"images": ["image.png"], "image_mode": "direct" if direct else "caption"},
        )

    async def test_late_caption_rebuilds_request_after_vision(self):
        harness, event, buffer = self.make_harness()
        with self.assertRaises(RequestCaptured):
            await harness._send_delayed_private_image_only_event(event, "u", buffer)
        req = harness.inject_humanized_state.await_args.args[1]
        self.assertNotIn("当前没有可靠视觉摘要", req.prompt)
        self.assertNotIn("否则只用一句", req.prompt)
        self.assertIn("最近对话", req.prompt)
        self.assertIn("一张写着", event.private_companion_delayed_image_vision_text)

    async def test_direct_image_request_does_not_claim_missing_vision(self):
        harness, event, buffer = self.make_harness(direct=True)
        with self.assertRaises(RequestCaptured):
            await harness._send_delayed_private_image_only_event(event, "u", buffer)
        req = harness.inject_humanized_state.await_args.args[1]
        self.assertNotIn("当前没有可靠视觉摘要", req.prompt)
        self.assertEqual(["image.png"], req.image_urls)

    async def test_hub_silence_prevents_runner_and_fallback(self):
        harness, event, buffer = self.make_harness()

        async def inject(current_event, req):
            current_event._private_companion_capability_decision = SimpleNamespace(
                reply=False
            )

        harness.inject_humanized_state = AsyncMock(side_effect=inject)
        harness._generate_private_image_fallback_reply = AsyncMock(
            return_value=("unexpected", "fallback")
        )
        with patch.object(
            image_module, "build_main_agent", new_callable=AsyncMock
        ) as build:
            await harness._send_delayed_private_image_only_event(event, "u", buffer)
        build.assert_not_awaited()
        harness._generate_private_image_fallback_reply.assert_not_awaited()

    async def test_buffered_sources_are_available_to_image_tools(self):
        harness = CommandHandlersMixin()
        event = SimpleNamespace(
            private_companion_delayed_image_sources=["image.png", "image.png"]
        )
        sources = await harness._photo_reference_sources_from_current_event(event, "u")
        self.assertEqual(["image.png"], sources)

    async def test_tool_delivered_image_does_not_trigger_fallback_text(self):
        harness, event, buffer = self.make_harness()

        async def inject(current_event, req):
            current_event._private_companion_photo_tool_sent = True

        harness.inject_humanized_state = AsyncMock(side_effect=inject)
        harness._generate_private_image_fallback_reply = AsyncMock(
            return_value=("unexpected", "fallback")
        )
        await harness._send_delayed_private_image_only_event(event, "u", buffer)
        harness._generate_private_image_fallback_reply.assert_not_awaited()
        self.assertTrue(buffer["delayed_reply_sent"])

    async def test_direct_mounted_image_has_no_blind_fallback(self):
        source = Path(image_module.__file__).with_name("passive_state_pipeline.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        inject = next(
            n
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "inject_humanized_state"
        )
        block = next(
            n
            for n in inject.body
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Name)
            and n.test.id == "buffered_images"
        )
        harness, event, buffer = self.make_harness(direct=True)
        harness._refresh_default_persona_prompt = AsyncMock()
        harness._private_image_direct_role_appearance_prompt = lambda: ""
        surface = SimpleNamespace(add=MagicMock())
        namespace = dict(
            self=harness,
            event=event,
            req=SimpleNamespace(image_urls=[]),
            buffered_images=buffer["images"],
            buffered_image_from_handoff=False,
            buffered_image_mode="direct",
            buffered_images_include_gif=False,
            buffered_image_vision="",
            user_id="u",
            prompt_surface=surface,
            logger=MagicMock(),
        )
        wrapper = ast.AsyncFunctionDef(
            name="run",
            args=ast.arguments(
                posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
            ),
            body=[ast.Global(names=["buffered_image_mode"]), block],
            decorator_list=[],
        )
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                str(source),
                "exec",
            ),
            namespace,
        )
        await namespace["run"]()
        self.assertNotIn(
            "image.fallback", [call.args[0] for call in surface.add.call_args_list]
        )
