# -*- coding: utf-8 -*-
"""持久化会话图片图库。

该模块只负责图片文件和索引，不负责事件提取或 LLM 意图判断。模型永远
只能拿到不透明 image_id，实际文件路径由本模块在受控目录内解析。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

try:
    from PIL import Image as PILImage
except Exception:  # pragma: no cover - 运行环境缺少 Pillow 时仍可保存原图
    PILImage = None


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


class ImageHistoryStore:
    """在插件数据目录中保存按会话隔离的图片历史。"""

    VERSION = 1

    def __init__(
        self,
        data_dir: str,
        data: dict[str, Any],
        *,
        retention_days: int = 7,
        max_mb: float = 512,
        save_callback: Callable[[], Any] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.root = (self.data_dir / "image_history").resolve()
        self.files_root = self.root / "files"
        self.thumbnails_root = self.root / "thumbnails"
        self.data = data if isinstance(data, dict) else {}
        self.retention_days = max(1, int(retention_days or 7))
        self.max_mb = max(0.0, float(max_mb or 0.0))
        self.save_callback = save_callback
        self._ensure_roots()
        self._ensure_state()

    def _ensure_roots(self) -> None:
        self.files_root.mkdir(parents=True, exist_ok=True)
        self.thumbnails_root.mkdir(parents=True, exist_ok=True)

    def _ensure_state(self) -> dict[str, Any]:
        state = self.data.get("image_history")
        if not isinstance(state, dict):
            state = {}
            self.data["image_history"] = state
        if not isinstance(state.get("items"), dict):
            state["items"] = {}
        state["version"] = self.VERSION
        return state

    def _items(self) -> dict[str, dict[str, Any]]:
        items = self._ensure_state().get("items")
        if not isinstance(items, dict):
            items = {}
            self.data["image_history"]["items"] = items
        return {key: value for key, value in items.items() if isinstance(value, dict)}

    def _save(self) -> None:
        callback = self.save_callback
        if not callable(callback):
            return
        try:
            callback()
        except Exception:
            # 图片收发不应因索引保存回调异常而崩溃；宿主会在下一次保存时重试。
            return

    @staticmethod
    def _scope_token(scope: str) -> str:
        return hashlib.sha256(_clean_text(scope, 240).encode("utf-8", errors="ignore")).hexdigest()[:24]

    def _scope_directory(self, scope: str) -> Path:
        return self.files_root / self._scope_token(scope)

    def _thumbnail_directory(self, scope: str) -> Path:
        return self.thumbnails_root / self._scope_token(scope)

    def _safe_path(self, relative_path: Any) -> Path | None:
        text = _clean_text(relative_path, 1000).replace("\\", "/")
        if not text or Path(text).is_absolute():
            return None
        try:
            candidate = (self.root / text).resolve()
            if not candidate.is_relative_to(self.root):
                return None
            return candidate
        except (OSError, ValueError):
            return None

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _image_info(path: Path) -> tuple[int, int, str]:
        suffix = path.suffix.lower()
        width = height = 0
        if PILImage is not None:
            try:
                with PILImage.open(path) as image:
                    width, height = int(image.width), int(image.height)
                    image.verify()
            except Exception:
                # 平台传入的文件可能没有完整扩展名；原图仍保留，后续发送链负责处理。
                width = height = 0
        return width, height, _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")

    def _create_thumbnail(self, source: Path, target: Path) -> tuple[str, int]:
        if PILImage is None:
            return "", 0
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            with PILImage.open(source) as image:
                image = image.convert("RGB")
                image.thumbnail((320, 320))
                image.save(partial, format="WEBP", quality=82, method=4)
            os.replace(partial, target)
            return str(target.relative_to(self.root)).replace("\\", "/"), target.stat().st_size
        except Exception:
            try:
                target.with_name(target.name + ".part").unlink(missing_ok=True)
            except OSError:
                pass
            return "", 0

    def add_file(
        self,
        scope: str,
        source_kind: str,
        sender_id: str,
        sender_name: str,
        source_path: str | Path,
        *,
        message_id: str = "",
        text: str = "",
        vision_summary: str = "",
        created_ts: float | None = None,
    ) -> dict[str, Any]:
        clean_scope = _clean_text(scope, 240)
        clean_kind = _clean_text(source_kind, 20).lower()
        if not clean_scope or clean_kind not in {"user", "bot"}:
            raise ValueError("图片历史 scope/source_kind 无效")
        source = Path(str(source_path)).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(str(source))
        suffix = source.suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            raise ValueError("图片历史只支持常见图片格式")
        digest, size = self._hash_file(source)
        items = self._ensure_state()["items"]
        clean_message_id = _clean_text(message_id, 160)
        for existing in items.values():
            if not isinstance(existing, dict):
                continue
            if (
                existing.get("scope") == clean_scope
                and existing.get("sha256") == digest
                and clean_message_id
                and existing.get("message_id") == clean_message_id
            ):
                return dict(existing)

        image_id = uuid.uuid4().hex[:24]
        day = time.strftime("%Y-%m-%d", time.localtime(created_ts or time.time()))
        target = self._scope_directory(clean_scope) / day / f"{image_id}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        try:
            if source != target:
                shutil.copy2(source, partial)
                os.replace(partial, target)
            thumb_rel, thumb_size = self._create_thumbnail(source, self._thumbnail_directory(clean_scope) / f"{image_id}.webp")
            width, height, mime = self._image_info(target)
            item = {
                "image_id": image_id,
                "scope": clean_scope,
                "source_kind": clean_kind,
                "sender_id": _clean_text(sender_id, 160),
                "sender_name": _clean_text(sender_name, 80),
                "message_id": clean_message_id,
                "created_ts": float(created_ts or time.time()),
                "path_rel": str(target.relative_to(self.root)).replace("\\", "/"),
                "thumbnail_rel": thumb_rel,
                "sha256": digest,
                "size_bytes": size,
                "thumbnail_size_bytes": thumb_size,
                "mime": mime,
                "width": width,
                "height": height,
                "text": _clean_text(text, 500),
                "vision_summary": _clean_text(vision_summary, 900),
            }
            items[image_id] = item
            self.cleanup(now=float(created_ts or time.time()), force=True)
            self._save()
            return dict(item)
        finally:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass

    def _remove_item(self, image_id: str, item: dict[str, Any]) -> None:
        for key in ("path_rel", "thumbnail_rel"):
            path = self._safe_path(item.get(key))
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self._ensure_state()["items"].pop(image_id, None)

    def resolve(self, scope: str, image_id: str) -> Path | None:
        clean_scope = _clean_text(scope, 240)
        key = _clean_text(image_id, 80)
        item = self._ensure_state()["items"].get(key)
        if not isinstance(item, dict) or item.get("scope") != clean_scope:
            return None
        now = time.time()
        if now - float(item.get("created_ts") or 0) > self.retention_days * 86400:
            return None
        path = self._safe_path(item.get("path_rel"))
        if path is None or not path.is_file():
            return None
        return path

    def list(
        self,
        scope: str,
        *,
        limit: int = 5,
        sender_id: str = "",
        query: str = "",
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        self.cleanup(now=now, force=False)
        clean_scope = _clean_text(scope, 240)
        clean_sender = _clean_text(sender_id, 160)
        terms = [part.casefold() for part in _clean_text(query, 160).split() if part]
        rows: list[dict[str, Any]] = []
        for item in self._ensure_state()["items"].values():
            if not isinstance(item, dict) or item.get("scope") != clean_scope:
                continue
            if clean_sender and item.get("sender_id") != clean_sender:
                continue
            if terms:
                haystack = " ".join(str(item.get(key) or "") for key in ("text", "vision_summary", "sender_name")).casefold()
                if not all(term in haystack for term in terms):
                    continue
            if self.resolve(clean_scope, str(item.get("image_id") or "")) is None:
                continue
            rows.append(dict(item))
        rows.sort(key=lambda item: float(item.get("created_ts") or 0), reverse=True)
        return rows[: max(1, min(int(limit or 5), 50))]

    def delete(
        self,
        scope: str,
        image_id: str,
        requester_id: str,
        *,
        is_admin: bool = False,
        is_owner: bool = False,
    ) -> dict[str, Any]:
        clean_scope = _clean_text(scope, 240)
        key = _clean_text(image_id, 80)
        item = self._ensure_state()["items"].get(key)
        if not isinstance(item, dict) or item.get("scope") != clean_scope:
            return {"ok": False, "status": "not_found", "message": "没有找到这张历史图片"}
        if not (is_admin or is_owner or item.get("sender_id") == _clean_text(requester_id, 160)):
            return {"ok": False, "status": "forbidden", "message": "只能删除自己发送的图片"}
        self._remove_item(key, item)
        self._save()
        return {"ok": True, "status": "deleted", "image_id": key}

    def cleanup(self, *, now: float | None = None, force: bool = False) -> dict[str, int]:
        current = float(now or time.time())
        state = self._ensure_state()
        items = state["items"]
        result = {"removed_by_age": 0, "removed_by_size": 0, "removed_missing": 0, "removed_partial": 0}
        changed = False
        for partial in self.root.rglob("*.part") if self.root.exists() else ():
            try:
                if current - partial.stat().st_mtime > 3600:
                    partial.unlink(missing_ok=True)
                    result["removed_partial"] += 1
            except OSError:
                pass
        for image_id, raw in list(items.items()):
            if not isinstance(raw, dict):
                items.pop(image_id, None)
                changed = True
                continue
            created = float(raw.get("created_ts") or 0)
            path = self._safe_path(raw.get("path_rel"))
            if current - created > self.retention_days * 86400:
                self._remove_item(image_id, raw)
                result["removed_by_age"] += 1
                changed = True
                continue
            if path is None or not path.is_file():
                items.pop(image_id, None)
                result["removed_missing"] += 1
                changed = True
        max_bytes = int(self.max_mb * 1024 * 1024) if self.max_mb > 0 else 0
        if max_bytes:
            live: list[tuple[float, int, str, dict[str, Any]]] = []
            total = 0
            for image_id, raw in items.items():
                path = self._safe_path(raw.get("path_rel"))
                thumb = self._safe_path(raw.get("thumbnail_rel"))
                size = (path.stat().st_size if path and path.is_file() else 0) + (thumb.stat().st_size if thumb and thumb.is_file() else 0)
                total += size
                live.append((float(raw.get("created_ts") or 0), size, image_id, raw))
            for _, size, image_id, raw in sorted(live):
                if total <= max_bytes:
                    break
                self._remove_item(image_id, raw)
                total -= size
                result["removed_by_size"] += 1
                changed = True
        if changed or force:
            self._save()
        return result

    def restore(self, *, now: float | None = None) -> dict[str, int]:
        self._ensure_roots()
        return self.cleanup(now=now, force=True)
