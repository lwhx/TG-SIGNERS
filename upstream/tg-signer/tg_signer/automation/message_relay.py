"""Configurable message relay for Telegram automation rules.

Rules select sources and filter messages. This handler selects delivery modes,
destinations, content extraction, templates, and deduplication policies.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        flags = re.IGNORECASE if target.get("ignore_case", True) else 0
        if mode == "extract" and target.get("all_matches", False):
            matches = list(re.finditer(pattern, text, flags=flags))[:20]
        else:
            match = re.search(pattern, text, flags=flags)
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
    if policy == "content" and value:
        identity = f"{mode}:content:{value}"
    else:
        # Message dedup is per rule; media without text uses message identity.
        identity = f"{mode}:message:{event.rule_id}:{source}:{index}"
    return RelayLedger.hash_value(identity)


async def relay_message(
    event: "Event", ctx: "AutomationContext", params: dict[str, Any]
) -> str:
    """Relay to up to 25 targets using forward/copy/text/extract/template."""
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
    for target in targets:
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
            ctx.log("relay_message: source and destination are the same", level="WARNING")
            failures = True
            continue
        try:
            normalized = dict(target, mode=mode)
            texts = _content_for_target(normalized, event.message, event, ctx)
            retry_after = max(0, int(target.get("retry_after_seconds", 600)))
            lease = max(30, int(target.get("lease_seconds", 300)))
        except (TypeError, ValueError, KeyError, IndexError, re.error) as exc:
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
        for index, text in enumerate(texts):
            if not text and mode not in {"forward", "copy"}:
                continue
            attempt = None
            fingerprint = ""
            if dedup != "off":
                if ledger is None:
                    ledger = _get_ledger(str(ctx.workdir))
                fingerprint = _fingerprint(
                    mode, dedup, event, event.message, text, index
                )
                reason, attempt = ledger.reserve(
                    destination, fingerprint, mode, str(event.chat_id),
                    getattr(event.message, "id", None), retry_after, lease,
                )
                if reason != "reserved":
                    ctx.log(
                        f"relay_message: skipped {reason} for {destination}",
                        level="INFO",
                    )
                    continue
            try:
                if mode == "forward":
                    await ctx.client.forward_messages(
                        chat_id, event.chat_id, event.message.id
                    )
                elif mode == "copy":
                    await ctx.client.copy_message(
                        chat_id, event.chat_id, event.message.id
                    )
                else:
                    await ctx.worker.send_message(chat_id, text)
            except Exception as exc:  # noqa: BLE001
                if attempt is not None:
                    ledger.finish(
                        destination, fingerprint, attempt, False, type(exc).__name__
                    )
                ctx.log(
                    f"relay_message: Telegram delivery failed ({type(exc).__name__})",
                    level="ERROR",
                )
                failures = True
                continue
            if attempt is not None:
                ledger.finish(destination, fingerprint, attempt, True)
            ctx.log(
                f"relay_message: delivered to {destination} via {mode}",
                level="INFO",
            )
    return "stop" if failures else "continue"
