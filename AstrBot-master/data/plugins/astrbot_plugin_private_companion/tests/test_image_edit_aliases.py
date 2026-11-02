import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin
from astrbot_plugin_private_companion.image_history import ImageHistoryStore
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class ImageEditAliasTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self):
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin._proactive_only_blocks_passive_event = lambda *args: False
        plugin._reaction_expression_authorization = lambda event: None
        plugin._pc_generate_photo_impl = AsyncMock(return_value='{"status":"success"}')
        return plugin

    async def test_image_edit_alias_uses_edit_authorization_and_workflow(self):
        plugin = self.make_plugin()
        event = SimpleNamespace(
            message_str="改成二次元风格图片",
            _private_companion_capability_decision=SimpleNamespace(actions=("image_edit",)),
        )
        result = await PrivateCompanionPlugin.pc_generate_photo.__wrapped__(
            plugin, event, prompt="将参考照片改绘为二次元插画", kind="image_edit"
        )
        self.assertEqual("success", json.loads(result)["status"])
        self.assertEqual("edit", plugin._pc_generate_photo_impl.await_args.kwargs["kind"])

    async def test_edit_alias_cannot_bypass_hub_authorization(self):
        plugin = self.make_plugin()
        for actions in (("image_generate",), ("text_only",), ()):
            with self.subTest(actions=actions):
                event = SimpleNamespace(
                    message_str="看看这个图片",
                    _private_companion_capability_decision=SimpleNamespace(actions=actions),
                )
                result = await PrivateCompanionPlugin.pc_generate_photo.__wrapped__(
                    plugin, event, prompt="改图", kind="image_edit"
                )
                self.assertEqual("hub_denied_image_edit", json.loads(result)["skip_reason"])
        plugin._pc_generate_photo_impl.assert_not_awaited()


class ImageHistoryReferenceTests(unittest.TestCase):
    def test_explicit_this_picture_reference_resolves_only_current_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.png"
            Image.new("RGB", (8, 8), "blue").save(source)
            store = ImageHistoryStore(temp, {})
            current = "bot-a:private:user-a"
            item = store.add_file(current, "user", "user-a", "", source)
            store.add_file("bot-b:private:user-a", "user", "user-a", "", source)
            store.add_file("bot-a:private:user-b", "user", "user-b", "", source)
            harness = CommandHandlersMixin()
            harness._image_history_store = store
            harness._event_scope_key = lambda event: event.scope
            for text in ("以这个图为参考图，生成头像", "参考这幅图的服装", "以此图为参考", "参考该图外观"):
                with self.subTest(text=text):
                    event = SimpleNamespace(scope=current, message_str=text)
                    sources = harness._photo_reference_sources_from_history(event, prompt="头像插画")
                    self.assertEqual([str(store.resolve(current, item["image_id"]))], sources)
            event = SimpleNamespace(scope="bot-c:private:user-a", message_str="以这个图为参考图")
            self.assertEqual([], harness._photo_reference_sources_from_history(event))

    def test_ordinary_chat_does_not_attach_history(self):
        for text in ("今天吃什么", "你喜欢这个风格吗", "参考答案是什么", "生成一张星空"):
            with self.subTest(text=text):
                self.assertFalse(CommandHandlersMixin._photo_reference_history_prompt_matches(text))
