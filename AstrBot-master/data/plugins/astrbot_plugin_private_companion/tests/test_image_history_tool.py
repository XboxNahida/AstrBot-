# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from astrbot_plugin_private_companion.image_history import ImageHistoryStore
from astrbot_plugin_private_companion.image_history_mixin import ImageHistoryMixin
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin


class _HistoryToolHarness(ImageHistoryMixin, LlmToolActionsMixin):
    def __init__(self, root: Path) -> None:
        self.data_dir = str(root)
        self.data = {}
        self._image_history_store = None
        self.enable_image_history = True
        self.image_history_retention_days = 7
        self.image_history_max_mb = 512
        self.admin_user_ids = ["admin"]
        self.deliveries: list[dict] = []

    def _event_scope_key(self, event) -> str:
        return event.scope

    def _event_sender_id(self, event) -> str:
        return event.sender_id

    def _event_permission_identity_id(self, event) -> str:
        return event.sender_id

    def _is_private_companion_owner_user_id(self, value) -> bool:
        return str(value) == "owner"

    def _is_configured_admin_user_id(self, value) -> bool:
        return str(value) == "admin"

    def _is_group_admin_event(self, event) -> bool:
        return bool(getattr(event, "group_admin", False))

    def _proactive_only_blocks_passive_event(self, event, feature) -> bool:
        return False

    async def _deliver_generated_image_to_event(self, event, *, image_path: str, caption: str = "") -> dict:
        self.deliveries.append({"event": event, "image_path": image_path, "caption": caption})
        return {"sent": True, "destination": "current", "message": "图片已发送"}


class ImageHistoryToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_and_use_return_opaque_ids_without_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.png"
            Image.new("RGB", (12, 8), (20, 30, 40)).save(source)
            harness = _HistoryToolHarness(root)
            harness._initialize_image_history_store()
            item = harness._image_history_store.add_file(
                "private:u1", "user", "u1", "小明", source, message_id="m1", text="夜景照片"
            )
            event = SimpleNamespace(scope="private:u1", sender_id="u1")

            listed = json.loads(await harness._pc_image_history_impl(event, action="list"))
            self.assertEqual("success", listed["status"])
            self.assertEqual(item["image_id"], listed["items"][0]["image_id"])
            self.assertNotIn("path_rel", listed["items"][0])
            self.assertNotIn(str(root), json.dumps(listed, ensure_ascii=False))

            used = json.loads(await harness._pc_image_history_impl(event, action="use", image_id=item["image_id"]))
            self.assertEqual("success", used["status"])
            self.assertEqual(item["image_id"], getattr(event, "_private_companion_image_history_selected_id"))

    async def test_send_delivers_selected_history_image_without_leaking_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "second.gif"
            Image.new("RGB", (12, 8), (20, 30, 40)).save(source, format="GIF")
            harness = _HistoryToolHarness(root)
            harness._initialize_image_history_store()
            item = harness._image_history_store.add_file(
                "private:u1", "user", "u1", "小明", source, message_id="m2"
            )
            event = SimpleNamespace(scope="private:u1", sender_id="u1")

            payload = json.loads(
                await harness._pc_image_history_impl(
                    event,
                    action="send",
                    image_id=item["image_id"],
                    caption="就是第二张。",
                )
            )

            self.assertEqual("success", payload["status"])
            self.assertTrue(payload["sent"])
            self.assertEqual(item["image_id"], payload["image_id"])
            self.assertEqual("就是第二张。", harness.deliveries[0]["caption"])
            self.assertEqual(
                harness._image_history_store.resolve("private:u1", item["image_id"]),
                Path(harness.deliveries[0]["image_path"]),
            )
            self.assertNotIn(str(root), json.dumps(payload, ensure_ascii=False))

    async def test_delete_obeys_sender_and_group_admin_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "photo.png"
            Image.new("RGB", (12, 8), (20, 30, 40)).save(source)
            harness = _HistoryToolHarness(root)
            harness._initialize_image_history_store()
            item = harness._image_history_store.add_file(
                "group:g1", "user", "u1", "", source, message_id="m1"
            )
            event = SimpleNamespace(scope="group:g1", sender_id="u2", group_admin=False)
            denied = json.loads(await harness._pc_image_history_impl(event, action="delete", image_id=item["image_id"]))
            self.assertEqual("forbidden", denied["status"])
            event.group_admin = True
            allowed = json.loads(await harness._pc_image_history_impl(event, action="delete", image_id=item["image_id"]))
            self.assertEqual("success", allowed["status"])


if __name__ == "__main__":
    unittest.main()
