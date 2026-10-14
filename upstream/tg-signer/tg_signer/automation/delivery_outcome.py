"""Conservative outcome classification for one bounded Telegram attempt."""

import json
from contextvars import ContextVar

from pyrogram import errors

relay_attempt = ContextVar("relay_attempt", default=False)
relay_rpc_state = ContextVar("relay_rpc_state", default=None)


class PreSendRejected(Exception):
    """A local guard rejected this attempt before any outbound send RPC."""


def configuration_digest(value):
    from .relay_ledger import RelayLedger

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return RelayLedger.hash_value(
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    )


def source_snapshot_digest(message):
    """Hash source text, entities and media identity without storing content."""
    snapshot = {
        key: str(getattr(message, key, None) or "")
        for key in ("text", "caption", "edit_date", "media", "media_group_id")
    }
    for key in ("entities", "caption_entities"):
        snapshot[key] = [
            {
                field: str(getattr(entity, field, None) or "")
                for field in (
                    "type",
                    "offset",
                    "length",
                    "url",
                    "language",
                    "custom_emoji_id",
                )
            }
            | {"user_id": str(getattr(getattr(entity, "user", None), "id", ""))}
            for entity in (getattr(message, key, None) or [])
        ]
    for key in (
        "photo",
        "video",
        "document",
        "audio",
        "voice",
        "animation",
        "sticker",
        "video_note",
    ):
        media = getattr(message, key, None)
        if media:
            snapshot[key] = getattr(media, "file_unique_id", None) or getattr(
                media, "file_id", None
            )
    for key in ("contact", "location", "venue", "poll", "dice", "game"):
        media = getattr(message, key, None)
        if media:
            snapshot[key] = str(media)
    return configuration_digest(snapshot)


def classify_failure(exc, *, single_attempt):
    if isinstance(exc, PreSendRejected):
        return "permanent", 0
    if single_attempt and isinstance(exc, errors.FloodWait):
        return "transient", max(1, int(getattr(exc, "value", 0) or 0)) + 1
    if single_attempt and isinstance(
        exc, (errors.BadRequest, errors.Forbidden, errors.Unauthorized)
    ):
        return "permanent", 0
    return "unknown", 0
