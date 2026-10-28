# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from astrbot_plugin_private_companion.image_history_mixin import ImageHistoryMixin


class _EventHistoryHarness(ImageHistoryMixin):
    def __init__(self, root: Path) -> None:
        self.data_dir = str(root)
        self.data: dict = {}
        self._image_history_store = None
        self.saved = 0

    def _save_data_sync(self) -> None:
        self.saved += 1

    def _event_scope_key(self, event) -> str:
        return event.scope

    def _event_sender_id(self, event) -> str:
        return event.sender_id

    def _sender_display_name(self, event) -> str:
        return event.sender_name

    def _event_message_id(self, event) -> str:
        return event.message_id

    def _event_raw_payload(self, event) -> dict:
        return event.raw

    def _event_text_for_recall_cache(self, event, *, limit=500) -> str:
        return event.text[:limit]

    async def _event_image_sources_for_recall_cache(self, event, *, limit=5):
        return [{"source": str(event.image), "tier": "local"}]


class ImageHistoryEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_user_and_bot_images_in_their_event_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "input.png"
            Image.new("RGB", (16, 16), (10, 20, 30)).save(image)
            harness = _EventHistoryHarness(root)
            harness._initialize_image_history_store()
            user_event = SimpleNamespace(
                scope="private:u1", sender_id="u1", sender_name="小明", message_id="m1",
                text="用户图片", image=image, raw={"post_type": "message"},
            )
            bot_event = SimpleNamespace(
                scope="private:u1", sender_id="bot", sender_name="Bot", message_id="m2",
                text="机器人图片", image=image, raw={"post_type": "message_sent"},
            )

            user_rows = await harness._record_event_image_history(user_event, source_kind="user")
            bot_rows = await harness._record_event_image_history(bot_event, source_kind="bot")

            self.assertEqual("user", user_rows[0]["source_kind"])
            self.assertEqual("bot", bot_rows[0]["source_kind"])
            self.assertEqual(2, len(harness._image_history_store.list("private:u1")))

    async def test_duplicate_event_does_not_add_second_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "input.png"
            Image.new("RGB", (16, 16), (10, 20, 30)).save(image)
            harness = _EventHistoryHarness(root)
            harness._initialize_image_history_store()
            event = SimpleNamespace(
                scope="group:g1", sender_id="u1", sender_name="", message_id="m1",
                text="图片", image=image, raw={"post_type": "message"},
            )
            await harness._record_event_image_history(event, source_kind="user")
            await harness._record_event_image_history(event, source_kind="user")
            self.assertEqual(1, len(harness._image_history_store.list("group:g1")))


if __name__ == "__main__":
    unittest.main()
