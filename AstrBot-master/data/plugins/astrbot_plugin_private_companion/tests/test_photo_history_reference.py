# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin
from astrbot_plugin_private_companion.image_history_mixin import ImageHistoryMixin


class _PhotoHistoryHarness(ImageHistoryMixin, CommandHandlersMixin):
    def __init__(self, root: Path) -> None:
        self.data_dir = str(root)
        self.data = {}
        self._image_history_store = None
        self.enable_image_history = True
        self.image_history_retention_days = 7
        self.image_history_max_mb = 512

    def _event_scope_key(self, event) -> str:
        return event.scope

    def _photo_reference_image_dir(self) -> Path:
        path = Path(self.data_dir) / "photo_reference_images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _photo_reference_stem(self, value: str) -> str:
        return "history_test"

    def _photo_reference_copy_local_file(self, source: Path, *, stem: str = "reference", trusted: bool = True) -> str:
        target = self._photo_reference_image_dir() / f"{stem}{source.suffix.lower()}"
        target.write_bytes(source.read_bytes())
        return str(target.resolve())

    async def _photo_reference_source_to_stable_path(self, source: str, *, stem: str = "reference", event=None, trusted: bool = True) -> str:
        return self._photo_reference_copy_local_file(Path(source), stem=stem, trusted=trusted)


class PhotoHistoryReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_previous_picture_uses_same_scope_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.png"
            Image.new("RGB", (12, 8), (20, 30, 40)).save(source)
            harness = _PhotoHistoryHarness(root)
            harness._initialize_image_history_store()
            item = harness._image_history_store.add_file(
                "private:u1", "user", "u1", "", source, message_id="m1"
            )
            event = SimpleNamespace(scope="private:u1")
            sources = harness._photo_reference_sources_from_history(event, "u1", "把刚才那张改成夜景")
            self.assertEqual([str(harness._image_history_store.resolve("private:u1", item["image_id"]))], sources)

    async def test_without_reference_word_does_not_use_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.png"
            Image.new("RGB", (12, 8), (20, 30, 40)).save(source)
            harness = _PhotoHistoryHarness(root)
            harness._initialize_image_history_store()
            harness._image_history_store.add_file("private:u1", "user", "u1", "", source, message_id="m1")
            event = SimpleNamespace(scope="private:u1")
            self.assertEqual([], harness._photo_reference_sources_from_history(event, "u1", "生成一张新的风景图"))

    async def test_edit_request_without_pronoun_uses_latest_same_scope_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.png"
            Image.new("RGB", (12, 8), (20, 30, 40)).save(source)
            harness = _PhotoHistoryHarness(root)
            harness._initialize_image_history_store()
            item = harness._image_history_store.add_file(
                "private:u1", "user", "u1", "", source, message_id="m1"
            )
            event = SimpleNamespace(scope="private:u1")
            sources = harness._photo_reference_sources_from_history(event, "u1", "把背景改成夜景")
            self.assertEqual([str(harness._image_history_store.resolve("private:u1", item["image_id"]))], sources)

    async def test_group_history_is_not_cross_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.png"
            Image.new("RGB", (12, 8), (20, 30, 40)).save(source)
            harness = _PhotoHistoryHarness(root)
            harness._initialize_image_history_store()
            harness._image_history_store.add_file("group:g1", "user", "u1", "", source, message_id="m1")
            event = SimpleNamespace(scope="group:g2")
            self.assertEqual([], harness._photo_reference_sources_from_history(event, "u2", "把刚才那张改一下"))

    async def test_command_context_copies_history_through_trusted_reference_sink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.png"
            Image.new("RGB", (12, 8), (20, 30, 40)).save(source)
            harness = _PhotoHistoryHarness(root)
            harness._initialize_image_history_store()
            harness._image_history_store.add_file("private:u1", "user", "u1", "", source, message_id="m1")
            event = SimpleNamespace(scope="private:u1")
            images, saw_image = await harness._photo_reference_images_from_command_context(
                event,
                "u1",
                limit=1,
                prompt="把刚才那张改一下",
            )
            self.assertTrue(saw_image)
            self.assertEqual("会话历史图片", images[0][1])
            self.assertTrue(Path(images[0][0]).is_file())
            self.assertTrue(Path(images[0][0]).is_relative_to(harness._photo_reference_image_dir().resolve()))


if __name__ == "__main__":
    unittest.main()
