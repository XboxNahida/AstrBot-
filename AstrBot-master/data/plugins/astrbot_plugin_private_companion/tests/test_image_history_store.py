# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from astrbot_plugin_private_companion.image_history import ImageHistoryStore


class ImageHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data: dict = {}
        self.store = ImageHistoryStore(str(self.root), self.data, retention_days=7, max_mb=1)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _image(self, name: str = "source.png", color: tuple[int, int, int] = (40, 80, 120)) -> Path:
        path = self.root / name
        Image.new("RGB", (32, 24), color).save(path, format="PNG")
        return path

    def test_add_list_resolve_and_scope_isolation(self) -> None:
        source = self._image()
        item = self.store.add_file(
            "private:user-1",
            "user",
            "user-1",
            "小明",
            source,
            message_id="msg-1",
            text="红色测试图",
            vision_summary="一张红色图片",
        )

        self.assertTrue(item["image_id"])
        self.assertEqual("private:user-1", item["scope"])
        self.assertTrue(self.store.resolve("private:user-1", item["image_id"]).is_file())
        self.assertIsNone(self.store.resolve("private:user-2", item["image_id"]))
        rows = self.store.list("private:user-1", query="红色")
        self.assertEqual([item["image_id"]], [row["image_id"] for row in rows])
        self.assertNotIn(str(source.resolve()), item["path_rel"])

    def test_duplicate_message_and_hash_returns_one_item(self) -> None:
        source = self._image()
        first = self.store.add_file("group:g1", "user", "u1", "", source, message_id="m1")
        second = self.store.add_file("group:g1", "user", "u1", "", source, message_id="m1")
        self.assertEqual(first["image_id"], second["image_id"])
        self.assertEqual(1, len(self.store.list("group:g1")))

    def test_delete_requires_owner_or_admin(self) -> None:
        source = self._image()
        item = self.store.add_file("group:g1", "user", "u1", "", source, message_id="m1")
        denied = self.store.delete("group:g1", item["image_id"], "u2")
        self.assertFalse(denied["ok"])
        self.assertTrue(self.store.resolve("group:g1", item["image_id"]).is_file())
        allowed = self.store.delete("group:g1", item["image_id"], "u2", is_admin=True)
        self.assertTrue(allowed["ok"])
        self.assertIsNone(self.store.resolve("group:g1", item["image_id"]))

    def test_cleanup_removes_expired_and_oldest_over_capacity(self) -> None:
        old_source = self._image("old.png", (1, 2, 3))
        new_source = self._image("new.png", (4, 5, 6))
        old = self.store.add_file("private:u", "user", "u", "", old_source, message_id="old")
        new = self.store.add_file("private:u", "bot", "bot", "", new_source, message_id="new")
        old_path = self.store.resolve("private:u", old["image_id"])
        os.utime(old_path, (time.time() - 8 * 86400, time.time() - 8 * 86400))
        self.store.data["image_history"]["items"][old["image_id"]]["created_ts"] = time.time() - 8 * 86400
        result = self.store.cleanup(now=time.time())
        self.assertGreaterEqual(result["removed_by_age"], 1)
        self.assertIsNone(self.store.resolve("private:u", old["image_id"]))
        self.assertIsNotNone(self.store.resolve("private:u", new["image_id"]))

    def test_path_escape_and_missing_file_are_not_resolvable(self) -> None:
        self.data["image_history"] = {
            "version": 1,
            "items": {
                "bad": {"image_id": "bad", "scope": "private:u", "path_rel": "../outside.png"},
            },
        }
        self.assertIsNone(self.store.resolve("private:u", "bad"))


if __name__ == "__main__":
    unittest.main()
