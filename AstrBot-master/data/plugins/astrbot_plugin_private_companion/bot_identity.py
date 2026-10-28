"""Resolve the selected persona before event wake and reply processing."""

from contextvars import ContextVar
from typing import Any

from astrbot.api import logger

ACTIVE_BOT_IDENTITY: ContextVar[tuple[Any, dict] | None] = ContextVar(
    "private_companion_bot_identity", default=None
)


async def bind_event_bot_identity(owner: Any, event: Any):
    """Bind the effective persona to this task without changing global settings.

    Args:
        owner: Plugin receiving the event.
        event: Event whose configuration and conversation select the persona.

    Returns:
        Context token to reset after processing the event.
    """
    identity = {}
    context = getattr(owner, "context", None)
    manager = getattr(context, "persona_manager", None)
    resolver = getattr(manager, "resolve_selected_persona", None)
    umo = str(getattr(event, "unified_msg_origin", "") or "")
    if callable(resolver) and umo:
        try:
            conversation = None
            conversations = getattr(context, "conversation_manager", None)
            if conversations is not None:
                cid = await conversations.get_curr_conversation_id(umo)
                if cid:
                    conversation = await conversations.get_conversation(umo, cid)
            settings_getter = getattr(
                owner, "_llm_request_provider_settings_for_event", None
            )
            settings = settings_getter(event) if callable(settings_getter) else {}
            persona_id, persona, _, _ = await resolver(
                umo=umo,
                conversation_persona_id=getattr(conversation, "persona_id", None),
                platform_name=getattr(event, "get_platform_name", lambda: "")(),
                provider_settings=settings,
            )
            specific_getter = getattr(owner, "_effective_plugin_persona_id", None)
            specific = specific_getter() if callable(specific_getter) else ""
            if specific:
                persona_id = specific
                persona = next(
                    (p for p in manager.personas_v3 if p.get("name") == specific), None
                )
            if isinstance(persona, dict):
                name = str(persona.get("name") or persona_id or "").strip()
                other_names = [
                    str(p.get("name") or "").strip()
                    for p in getattr(manager, "personas_v3", [])
                    if isinstance(p, dict) and p.get("name") and p.get("name") != name
                ]
                identity = {
                    "name": name,
                    "persona_id": str(persona_id or ""),
                    "other_persona_names": other_names,
                }
                setattr(
                    event,
                    "_private_companion_emotion_persona",
                    str(persona.get("prompt") or ""),
                )
                setattr(
                    event,
                    "_private_companion_emotion_persona_id",
                    str(persona_id or ""),
                )
        except Exception as exc:
            logger.warning(
                "Bot identity resolution failed: error=%s", type(exc).__name__
            )
    setattr(event, "_private_companion_bot_identity", identity)
    return ACTIVE_BOT_IDENTITY.set((owner, identity))
