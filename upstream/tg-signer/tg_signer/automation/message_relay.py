"""Configurable message relay for Telegram automation rules.

Rules select sources and filter messages. This handler selects delivery modes,
destinations, content extraction, templates, and deduplication policies.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from itertools import islice
from pathlib import Path
from secrets import token_hex
from typing import TYPE_CHECKING, Any

import regex

from .delivery_outcome import (
    classify_failure,
    configuration_digest,
    relay_attempt,
    relay_rpc_state,
    source_snapshot_digest,
)
from .media_groups import album_fingerprint, prepare_album
from .relay_ledger import RelayLedger

if TYPE_CHECKING:
    from .models import AutomationContext, Event

_MODES = {"forward", "copy", "text", "extract", "template"}
_DEDUP = {"message", "content", "off"}


@lru_cache(maxsize=16)
def _get_ledger(workdir: str) -> RelayLedger:
    return RelayLedger(Path(workdir))


def _message_text(message: Any) -> str:
    return getattr(message, "text", None) or getattr(message, "caption", None) or ""


def _is_own_message(message: Any) -> bool:
    user = getattr(message, "from_user", None)
    return bool(getattr(message, "outgoing", False) or getattr(user, "is_self", False))


def _content_for_target(
    target: dict[str, Any], message: Any, event: Any, ctx: Any
) -> list[str]:
    text = _message_text(message)
    mode = target["mode"]
    if mode in {"forward", "copy"}:
        return []
    if mode == "text":
        return [text] if text else []

    pattern = target.get("pattern")
    match = None
    if pattern is not None:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("pattern must be a nonempty string")
        flags = regex.IGNORECASE if target.get("ignore_case", True) else 0
        if mode == "extract" and target.get("all_matches", False):
            # Bound both regex CPU time and the number of extracted matches.
            matches = list(
                islice(regex.finditer(pattern, text, flags=flags, timeout=0.2), 20)
            )
        else:
            match = regex.search(pattern, text, flags=flags, timeout=0.2)
            matches = [match] if match else []
        if not matches:
            return []
    elif mode == "extract":
        raise ValueError("extract mode requires pattern")

    if mode == "extract":
        output = []
        for found in matches:
            group = target.get("group", 1 if found.lastindex else 0)
            result = found.group(group)
            if result:
                output.append(result)
        return output

    if mode == "template":
        template = target.get("template")
        if not isinstance(template, str) or not template:
            raise ValueError("template mode requires template")
        mapping: dict[str, Any] = {
            **ctx.vars,
            "text": text,
            "chat_id": event.chat_id,
            "message_id": getattr(message, "id", None),
            "rule_id": event.rule_id,
            "sender_id": getattr(getattr(message, "from_user", None), "id", ""),
        }
        if match:
            mapping["match"] = match.group(0)
            mapping.update(match.groupdict())
            for idx, value in enumerate(match.groups(), 1):
                mapping[f"group{idx}"] = value or ""
        return [template.format_map(mapping)]
    raise ValueError(f"unsupported relay mode {mode}")


def _fingerprint(
    mode: str, policy: str, event: Any, message: Any, text: str, index: int
) -> str:
    source = f"{event.chat_id}:{getattr(message, 'id', None)}"
    value = text or _message_text(message)
    # Caption-only fingerprints cannot identify images or files. For media
    # forward/copy use the source message identity even with content dedup.
    has_media = bool(
        getattr(message, "media", None)
        or any(
            getattr(message, key, None)
            for key in (
                "photo",
                "video",
                "document",
                "audio",
                "voice",
                "animation",
                "sticker",
            )
        )
    )
    if (
        policy == "content"
        and value
        and not (mode in {"forward", "copy"} and has_media)
    ):
        identity = f"{mode}:content:{value}"
    else:
        # Message dedup is per rule; media without text uses message identity.
        identity = f"{mode}:message:{event.rule_id}:{source}:{index}"
    return RelayLedger.hash_value(identity)


async def relay_message(
    event: "Event",
    ctx: "AutomationContext",
    params: dict[str, Any],
    *,
    retry_ref: tuple[str, str, int, int, str] | None = None,
    retry_claim: str | None = None,
) -> str:
    """Relay to up to 25 targets using forward/copy/text/extract/template.

    The keyword-only retry_ref is an internal server-side safety guard:
    (destination, fingerprint, target_index, part_index, payload_digest).
    It is never read from persisted Telegram rule params.
    """
    if event.message is None:
        return "stop"
    if params.get("ignore_self", True) and _is_own_message(event.message):
        return "continue"
    targets = params.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > 25:
        ctx.log("relay_message: targets must contain 1-25 entries", level="WARNING")
        return "stop"

    ledger = None
    failures = False
    for target_index, target in enumerate(targets):
        if retry_ref is not None and target_index != retry_ref[2]:
            continue
        if not isinstance(target, dict):
            failures = True
            continue
        chat_id = target.get("chat_id")
        mode = target.get("mode", "forward")
        dedup = target.get("dedup", "message")
        if not isinstance(chat_id, (str, int)) or not str(chat_id).strip():
            ctx.log("relay_message: missing target chat_id", level="WARNING")
            failures = True
            continue
        if (
            not isinstance(mode, str)
            or not isinstance(dedup, str)
            or mode not in _MODES
            or dedup not in _DEDUP
        ):
            ctx.log("relay_message: unknown mode or dedup policy", level="WARNING")
            failures = True
            continue
        if str(chat_id).strip().lower() == str(event.chat_id).strip().lower():
            ctx.log(
                "relay_message: source and destination are the same", level="WARNING"
            )
            failures = True
            continue

        worker = ctx.worker
        task_name = str(getattr(worker, "task_name", ""))
        task_id = int(task_name) if task_name.isdecimal() else None
        account_id = getattr(worker, "account_id", None)
        album = None
        album_requested = (
            retry_ref is None
            and mode in {"forward", "copy"}
            and target.get("album_mode", "single") == "album"
            and getattr(event.message, "media_group_id", None) is not None
        )
        if album_requested:
            if ledger is None:
                ledger = _get_ledger(str(ctx.workdir))
            album_key = album_fingerprint(
                account_id=account_id,
                task_id=task_id,
                rule_id=event.rule_id,
                source_chat=str(event.chat_id),
                media_group_id=str(event.message.media_group_id),
                target_index=target_index,
                mode=mode,
            )
            # Another member may already have reserved/sent the entire album.
            # Failed/uncertain albums require manual investigation, never an
            # automatic resend of one missing-looking member.
            if ledger.get(str(chat_id).strip().lower(), album_key) is not None:
                continue
            try:
                album = await prepare_album(ctx.client, event, key=album_key)
            except Exception as exc:  # noqa: BLE001
                ctx.log(
                    f"relay_message: album preflight failed ({type(exc).__name__})",
                    level="WARNING",
                )
                failures = True
                continue
        try:
            normalized = dict(target, mode=mode)
            texts = _content_for_target(normalized, event.message, event, ctx)
            retry_after = max(0, int(target.get("retry_after_seconds", 600)))
            lease = max(30, int(target.get("lease_seconds", 300)))
        except (
            TypeError,
            ValueError,
            KeyError,
            IndexError,
            regex.error,
            TimeoutError,
        ) as exc:
            ctx.log(
                f"relay_message: invalid rule ({type(exc).__name__})",
                level="ERROR",
            )
            failures = True
            continue
        if mode in {"forward", "copy"}:
            texts = [""]
        if not texts:
            continue
        destination = str(chat_id).strip().lower()
        if retry_ref is not None and destination != retry_ref[0]:
            continue
        for index, text in enumerate(texts):
            if retry_ref is not None and index != retry_ref[3]:
                continue
            if not text and mode not in {"forward", "copy"}:
                continue
            if ledger is None:
                ledger = _get_ledger(str(ctx.workdir))
            # Hash the exact text used to send, never persist its plaintext.
            payload_digest = (
                album.digest
                if album is not None
                else RelayLedger.hash_value(
                    mode
                    + ":"
                    + (
                        source_snapshot_digest(event.message)
                        if mode in {"forward", "copy"}
                        else text
                    )
                )
            )
            fingerprint = (
                album.fingerprint
                if album is not None
                else (
                    token_hex(32)
                    if dedup == "off" and retry_ref is None
                    else _fingerprint(mode, dedup, event, event.message, text, index)
                )
            )
            if retry_ref is not None:
                # No replay if the source message was edited or the target's
                # configured action/regex now produces different content.
                if payload_digest != retry_ref[4]:
                    ctx.log("relay_message: original payload changed", level="WARNING")
                    continue
                if dedup != "off" and fingerprint != retry_ref[1]:
                    ctx.log("relay_message: fingerprint changed", level="WARNING")
                    continue
                fingerprint = retry_ref[1]
            with ledger.active_delivery(destination, fingerprint) as active:
                if not active:
                    continue
                reason, attempt = ledger.reserve(
                    destination,
                    fingerprint,
                    "album_" + mode if album is not None else mode,
                    str(event.chat_id),
                    album.message_ids[0] if album is not None else getattr(event.message, "id", None),
                    retry_after,
                    lease,
                    force_failed=retry_ref is not None,
                    account_id=account_id,
                    task_id=task_id,
                    rule_id=event.rule_id,
                    handler_index=getattr(ctx, "handler_index", None),
                    target_index=target_index,
                    part_index=index,
                    dedup=dedup,
                    payload_digest=payload_digest,
                    config_digest=configuration_digest(
                        getattr(worker, "config", params)
                    ),
                    source_digest=source_snapshot_digest(event.message),
                    retry_claim=retry_claim,
                    media_count=len(album.message_ids) if album is not None else 1,
                    media_group_key=album.group_key if album is not None else None,
                )
                if reason != "reserved":
                    ctx.log(
                        f"relay_message: skipped {reason} for {destination}",
                        level="INFO",
                    )
                    continue
                rpc_state = {"accepted": False}
                try:

                    async def send(
                        mode=mode, chat_id=chat_id, text=text,
                        rpc_state=rpc_state, album=album,
                    ):
                        guard = getattr(ctx.worker, "relay_before_send", None)
                        if guard:
                            guard()
                        token = relay_attempt.set(True)
                        state_token = relay_rpc_state.set(rpc_state)
                        try:
                            if album is not None and mode == "forward":
                                return await ctx.client.forward_messages(
                                    chat_id, event.chat_id, list(album.message_ids)
                                )
                            if album is not None and mode == "copy":
                                return await ctx.client.copy_media_group(
                                    chat_id, event.chat_id, album.message_ids[0]
                                )
                            if mode == "forward":
                                return await ctx.client.forward_messages(
                                    chat_id, event.chat_id, event.message.id
                                )
                            if mode == "copy":
                                copy = getattr(event.message, "copy", None)
                                if copy:
                                    return await copy(chat_id)
                                return await ctx.client.copy_message(
                                    chat_id, event.chat_id, event.message.id
                                )
                            sender = (
                                getattr(ctx.client, "send_message", None)
                                or ctx.worker.send_message
                            )
                            return await sender(chat_id, text)
                        finally:
                            relay_attempt.reset(token)
                            relay_rpc_state.reset(state_token)

                    wrapper = getattr(ctx.worker, "_call_telegram_api", None)
                    call = (
                        wrapper(
                            "relay." + ("album_" if album is not None else "") + mode,
                            send,
                            retry_on_floodwait=False,
                        )
                        if wrapper
                        else send()
                    )
                    result = await asyncio.wait_for(call, timeout=90)
                    # A response without a message identifier cannot prove delivery.
                    messages = result if isinstance(result, list) else [result]
                    if album is not None and (
                        len(messages) != len(album.message_ids)
                        or any(not getattr(item, "id", None) for item in messages)
                    ):
                        raise RuntimeError("UnconfirmedAlbumResponse")
                    message_id = next(
                        (
                            getattr(m, "id", None)
                            for m in messages
                            if getattr(m, "id", None)
                        ),
                        None,
                    )
                    if message_id is None:
                        raise RuntimeError("UnconfirmedTelegramResponse")
                except asyncio.CancelledError:
                    ledger.finish(
                        destination,
                        fingerprint,
                        attempt,
                        False,
                        "CancelledSend",
                        failure_kind="unknown",
                    )
                    raise
                except Exception as exc:  # noqa: BLE001
                    failure_kind, wait = classify_failure(
                        exc,
                        single_attempt=bool(
                            getattr(ctx.client, "relay_single_attempt", False)
                        )
                        and not rpc_state["accepted"],
                    )
                    ledger.finish(
                        destination,
                        fingerprint,
                        attempt,
                        False,
                        type(exc).__name__,
                        failure_kind=failure_kind,
                        retry_after_seconds=wait,
                    )
                    ctx.log(
                        f"relay_message: Telegram delivery failed ({type(exc).__name__})",
                        level="ERROR",
                    )
                    failures = True
                    continue
                ledger.finish(
                    destination,
                    fingerprint,
                    attempt,
                    True,
                    destination_message=message_id,
                )
                if target.get("track_bot_result") and account_id is not None:
                    bot_chat = getattr(getattr(messages[0], "chat", None), "id", None)
                    if isinstance(bot_chat, int):
                        # Receipt-tracking failure never converts an already
                        # confirmed Telegram send into a failed delivery.
                        try:
                            ledger.track_bot_send(
                                destination=destination,
                                fingerprint=fingerprint,
                                account_id=account_id,
                                task_id=task_id,
                                bot_chat=bot_chat,
                                sent_message=message_id,
                                success_pattern=target.get("bot_success_pattern", ""),
                                failure_pattern=target.get("bot_failure_pattern", ""),
                                timeout_minutes=target.get("bot_timeout_minutes", 120),
                            )
                        except Exception as exc:  # noqa: BLE001
                            ctx.log(
                                f"relay_message: Bot receipt tracking unavailable "
                                f"({type(exc).__name__})",
                                level="WARNING",
                            )
                ctx.log(
                    f"relay_message: delivered to {destination} via {mode}",
                    level="INFO",
                )
    return "stop" if failures else "continue"
