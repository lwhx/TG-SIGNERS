"""P4: uncertain outcomes must never become sendable merely by waiting."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from pyrogram import errors

from tg_signer.automation.message_relay import relay_message
from tg_signer.automation.relay_ledger import RelayLedger


def linked(ledger, key="one"):
    return ledger.reserve(
        "@target",
        key,
        "text",
        "-100",
        1,
        account_id=1,
        task_id=2,
        rule_id="r",
        handler_index=0,
        target_index=0,
        part_index=0,
        dedup="message",
        payload_digest="a" * 64,
    )


def expire(ledger):
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE relay_deliveries SET updated_at=1,lease_until=2")


def test_expired_send_is_uncertain_and_cannot_be_reserved(tmp_path):
    ledger = RelayLedger(tmp_path)
    linked(ledger)
    expire(ledger)
    assert ledger.recover_stale() == 1
    row = ledger.get("@target", "one")
    assert row["status"] == "uncertain" and not row["retryable"]
    assert linked(ledger) == ("uncertain", None)
    assert ledger.stats()["uncertain"] == 1


def test_late_success_resolves_expired_send_and_defeats_stale_review(tmp_path):
    ledger = RelayLedger(tmp_path)
    linked(ledger)
    expire(ledger)
    ledger.recover_stale()
    ledger.finish("@target", "one", 1, True)
    assert ledger.get("@target", "one")["status"] == "sent"
    assert not ledger.resolve("@target", "one", 1, "not_sent")
    assert linked(ledger) == ("duplicate", None)


def test_late_success_overrides_not_sent_review_for_same_attempt(tmp_path):
    ledger = RelayLedger(tmp_path)
    linked(ledger)
    expire(ledger)
    ledger.recover_stale()
    assert ledger.resolve("@target", "one", 1, "not_sent")
    ledger.finish("@target", "one", 1, True, destination_message=42)
    assert ledger.get("@target", "one")["status"] == "sent"
    assert ledger.reserve("@target", "one", "text", "-100", 1, force_failed=True) == (
        "duplicate",
        None,
    )


@pytest.mark.asyncio
async def test_active_send_cannot_be_reviewed_after_lease_expires(tmp_path):
    ctx = Context(tmp_path, None)
    ledger = RelayLedger(tmp_path)

    async def slow_send(*args):
        expire(ledger)
        ledger.recover_stale()
        row = ledger.history()["items"][0]
        assert not ledger.resolve("@target", row["fingerprint"], 1, "not_sent")
        return SimpleNamespace(id=42)

    ctx.client.send_message = slow_send
    await relay_message(
        event(), ctx, {"targets": [{"chat_id": "@target", "mode": "text"}]}
    )
    assert ledger.history()["items"][0]["status"] == "sent"


@pytest.mark.asyncio
async def test_media_change_blocks_manual_replay_and_copy_uses_snapshot(tmp_path):
    ctx = Context(tmp_path, None)
    msg = event()
    msg.message.photo = SimpleNamespace(file_unique_id="A", file_id="fileA")
    msg.message.caption = "same caption"
    msg.message.text = None
    calls = []

    async def copy_snapshot(*args):
        calls.append(msg.message.photo.file_unique_id)
        if len(calls) == 1:
            raise errors.FloodWait(10)
        return SimpleNamespace(id=42)

    async def refetch_copy(*args):
        pytest.fail("copy must use the verified Message snapshot")

    msg.message.copy = copy_snapshot
    ctx.client.copy_message = refetch_copy
    params = {"targets": [{"chat_id": "@target", "mode": "copy"}]}
    await relay_message(msg, ctx, params)
    row = RelayLedger(tmp_path).history()["items"][0]
    assert row["retry_status"] == "manual_only" and row["next_retry_at"] is None
    msg.message.photo.file_unique_id = "B"
    await relay_message(
        msg,
        ctx,
        params,
        retry_ref=(
            "@target",
            row["fingerprint"],
            0,
            0,
            row["payload_digest"],
        ),
    )
    assert calls == ["A"]
    msg.message.photo.file_unique_id = "A"
    await relay_message(
        msg,
        ctx,
        params,
        retry_ref=(
            "@target",
            row["fingerprint"],
            0,
            0,
            row["payload_digest"],
        ),
    )
    assert calls == ["A", "A"]
    assert RelayLedger(tmp_path).history()["items"][0]["status"] == "sent"


def test_review_only_unlocks_manual_retry_and_preserves_audit(tmp_path):
    ledger = RelayLedger(tmp_path)
    linked(ledger)
    expire(ledger)
    ledger.recover_stale()
    assert not ledger.resolve("@target", "one", 99, "not_sent")
    assert ledger.resolve("@target", "one", 1, "not_sent")
    row = ledger.get("@target", "one")
    assert row["retryable"] and row["resolution"] == "not_sent"
    assert row["next_retry_at"] is None and row["resolved_at"]
    assert linked(ledger) == ("review_required", None)
    assert ledger.reserve("@target", "one", "text", "-100", 1, force_failed=True) == (
        "reserved",
        2,
    )


def test_unclassified_old_failure_requires_review(tmp_path):
    ledger = RelayLedger(tmp_path)
    linked(ledger)
    ledger.finish("@target", "one", 1, False, "TimeoutError")
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE relay_deliveries SET failure_kind=NULL")
    row = ledger.get("@target", "one")
    assert row["needs_review"] and not row["retryable"]
    assert linked(ledger) == ("review_required", None)


class Context:
    def __init__(self, directory, exc):
        self.workdir = directory
        self.vars = {}
        self.handler_index = 0
        self.calls = 0
        self.exc = exc
        self.client = SimpleNamespace(send_message=self.send, relay_single_attempt=True)
        self.worker = SimpleNamespace(task_name="2", account_id=1)

    def log(self, *args, **kwargs):
        pass

    async def send(self, *args, **kwargs):
        self.calls += 1
        if self.exc:
            raise self.exc
        return SimpleNamespace(id=42)


def event():
    return SimpleNamespace(
        message=SimpleNamespace(id=1, text="test", caption=None),
        chat_id=-100,
        rule_id="r",
        type="message",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [TimeoutError(), ConnectionError(), RuntimeError()])
async def test_ambiguous_failure_is_not_resent_on_duplicate(tmp_path, exc):
    ctx = Context(tmp_path, exc)
    params = {
        "targets": [{"chat_id": "@target", "mode": "text", "retry_after_seconds": 0}]
    }
    await relay_message(event(), ctx, params)
    await relay_message(event(), ctx, params)
    assert ctx.calls == 1
    assert RelayLedger(tmp_path).history()["items"][0]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_cancelled_send_is_uncertain(tmp_path):
    ctx = Context(tmp_path, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await relay_message(
            event(), ctx, {"targets": [{"chat_id": "@target", "mode": "text"}]}
        )
    assert RelayLedger(tmp_path).history()["items"][0]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_explicit_floodwait_rejection_has_persistent_retry_floor(tmp_path):
    ctx = Context(tmp_path, errors.FloodWait(77))
    await relay_message(
        event(),
        ctx,
        {"targets": [{"chat_id": "@target", "mode": "text", "retry_after_seconds": 0}]},
    )
    row = RelayLedger(tmp_path).history()["items"][0]
    assert row["status"] == "failed" and row["failure_kind"] == "transient"
    assert row["next_retry_at"] - row["updated_at"] >= 77


@pytest.mark.asyncio
async def test_post_send_rpc_error_does_not_become_safe_retry(tmp_path):
    from tg_signer.automation.delivery_outcome import relay_rpc_state

    ctx = Context(tmp_path, None)

    async def accepted_then_failed(*args):
        ctx.calls += 1
        relay_rpc_state.get()["accepted"] = True
        raise errors.FloodWait(10)

    ctx.client.send_message = accepted_then_failed
    await relay_message(
        event(), ctx, {"targets": [{"chat_id": "@target", "mode": "text"}]}
    )
    assert RelayLedger(tmp_path).history()["items"][0]["status"] == "uncertain"
