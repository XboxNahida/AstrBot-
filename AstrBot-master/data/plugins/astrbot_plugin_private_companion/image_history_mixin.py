# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from .image_history import ImageHistoryStore


class ImageHistoryMixin:
    """把平台事件中的真实图片接入持久历史图库。"""

    def _initialize_image_history_store(self) -> ImageHistoryStore:
        store = getattr(self, "_image_history_store", None)
        if isinstance(store, ImageHistoryStore):
            return store
        store = ImageHistoryStore(
            str(getattr(self, "data_dir", "") or "."),
            getattr(self, "data", {}),
            retention_days=int(getattr(self, "image_history_retention_days", 7) or 7),
            max_mb=float(getattr(self, "image_history_max_mb", 512) or 512),
            save_callback=getattr(self, "_save_data_sync", None),
        )
        self._image_history_store = store
        result = store.restore()
        if any(int(value or 0) for value in result.values()):
            try:
                from astrbot.api import logger

                logger.info(
                    "[PrivateCompanion] 历史图片图库启动恢复: age=%s size=%s missing=%s partial=%s",
                    result.get("removed_by_age", 0),
                    result.get("removed_by_size", 0),
                    result.get("removed_missing", 0),
                    result.get("removed_partial", 0),
                )
            except Exception:
                pass
        return store

    def _image_history_store_or_none(self) -> ImageHistoryStore | None:
        try:
            return self._initialize_image_history_store()
        except Exception:
            return None

    async def _record_event_image_history(
        self,
        event: Any,
        *,
        source_kind: str,
    ) -> list[dict[str, Any]]:
        store = self._image_history_store_or_none()
        if store is None:
            return []
        scope_getter = getattr(self, "_event_scope_key", None)
        scope = str(scope_getter(event) if callable(scope_getter) else getattr(event, "unified_msg_origin", "") or "").strip()
        if not scope:
            return []
        sender_getter = getattr(self, "_event_sender_id", None)
        sender_id = str(sender_getter(event) if callable(sender_getter) else "").strip()
        display_getter = getattr(self, "_sender_display_name", None)
        sender_name = str(display_getter(event) if callable(display_getter) else "").strip()
        message_getter = getattr(self, "_event_message_id", None)
        message_id = str(message_getter(event) if callable(message_getter) else "").strip()
        text_getter = getattr(self, "_event_text_for_recall_cache", None)
        text = str(text_getter(event, limit=500) if callable(text_getter) else getattr(event, "message_str", "") or "").strip()
        source_getter = getattr(self, "_event_image_sources_for_recall_cache", None)
        if not callable(source_getter):
            return []
        try:
            raw_sources = await source_getter(event, limit=5)
        except Exception:
            return []
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_sources if isinstance(raw_sources, list) else []:
            if isinstance(raw, dict):
                source = str(raw.get("source") or "").strip()
                tier = str(raw.get("tier") or "").strip().lower()
            else:
                source = str(raw or "").strip()
                tier = "local"
            if not source or tier not in {"local", "url"} or source in seen:
                continue
            seen.add(source)
            try:
                item = store.add_file(
                    scope,
                    source_kind,
                    sender_id,
                    sender_name,
                    source,
                    message_id=message_id,
                    text=text,
                )
            except (OSError, ValueError):
                continue
            rows.append(item)
        return rows

    async def _record_inbound_image_history(self, event: Any) -> list[dict[str, Any]]:
        return await self._record_event_image_history(event, source_kind="user")

    async def _record_outbound_image_history(self, event: Any) -> list[dict[str, Any]]:
        return await self._record_event_image_history(event, source_kind="bot")
