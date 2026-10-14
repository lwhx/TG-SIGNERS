"""Bounded P7 Telegram album preparation with stable group idempotency keys."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .delivery_outcome import source_snapshot_digest
from .relay_ledger import RelayLedger


@dataclass(frozen=True)
class RelayAlbum:
    message_ids: tuple[int, ...]
    fingerprint: str
    digest: str
    group_key: str


def album_fingerprint(
    *, account_id: int | None, task_id: int | None, rule_id: str,
    source_chat: str, media_group_id: str, target_index: int, mode: str,
) -> str:
    """Same key for each album item, even if target dedup is disabled.

    Do not use the message ID, which varies across album update callbacks.
    Include account/task/rule/target to avoid cross-task collisions.
    """
    return RelayLedger.hash_value(
        "album:v1:"
        + ":".join(map(str, (
            account_id, task_id, rule_id, source_chat, media_group_id, target_index, mode,
        )))
    )


async def prepare_album(client: Any, event: Any, *, key: str) -> RelayAlbum:
    """Fetch all members before any RPC that sends an album.

    A missing, partial, or inconsistent group fails closed: never fall back
    to sending a single album member. A subsequent incoming member may retry
    the preflight, while the durable delivery ledger guards actual sends.
    """
    message = event.message
    expected = str(getattr(message, "media_group_id", "") or "")
    if not expected or getattr(message, "id", 0) <= 0:
        raise ValueError("NotAnAlbum")
    get_group = getattr(client, "get_media_group", None)
    if not callable(get_group):
        raise ValueError("MediaGroupUnsupported")
    # Give Telegram time to receive all album parts before fetching members.
    await asyncio.sleep(1)
    raw = await asyncio.wait_for(
        get_group(event.chat_id, message.id), timeout=20
    )
    # Telegram does not expose an album total in each update. Read twice
    # after a settle interval, failing closed if additional parts arrive.
    await asyncio.sleep(1)
    repeated = await asyncio.wait_for(
        get_group(event.chat_id, message.id), timeout=20
    )
    observed = tuple(sorted(getattr(item, "id", 0) for item in raw))
    stable = tuple(sorted(getattr(item, "id", 0) for item in repeated))
    if observed != stable:
        raise ValueError("MediaGroupStillArriving")
    members = sorted(list(repeated), key=lambda item: getattr(item, "id", 0))
    ids = tuple(getattr(item, "id", 0) for item in members)
    if not 2 <= len(ids) <= 10 or len(set(ids)) != len(ids) or min(ids) <= 0:
        raise ValueError("IncompleteMediaGroup")
    if message.id not in ids or any(
        str(getattr(item, "media_group_id", "") or "") != expected
        or getattr(getattr(item, "chat", None), "id", event.chat_id) != event.chat_id
        for item in members
    ):
        raise ValueError("InconsistentMediaGroup")
    digest = RelayLedger.hash_value(
        "album-payload:v1:" + ":".join(source_snapshot_digest(item) for item in members)
    )
    return RelayAlbum(ids, key, digest, expected)
