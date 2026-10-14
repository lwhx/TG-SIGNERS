"""Focused tests for the generic Telegram message relay."""

import sqlite3
from types import SimpleNamespace

import pytest

from tg_signer.automation.message_relay import relay_message
from tg_signer.automation.relay_ledger import RelayLedger


class Worker:
    def __init__(self):
        self.sent = []
        self.fail = False
        self.task_name = "3"
        self.account_id = 4

    async def send_message(self, target, text):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append((target, text))


class Client:
    def __init__(self):
        self.forwarded = []
        self.copied = []

    async def forward_messages(self, target, source, message_id):
        self.forwarded.append((target, source, message_id))

    async def copy_message(self, target, source, message_id):
        self.copied.append((target, source, message_id))


class Context:
    def __init__(self, directory):
        self.workdir = directory
        self.worker = Worker()
        self.client = Client()
        self.vars = {"note": "example"}
        self.logs = []
        self.handler_index = 0

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))


def event(text="hello https://115cdn.com/s/ABC?password=999#", chat_id=-100,
          message_id=10, caption=None, own=False):
    message = SimpleNamespace(
        text=text,
        caption=caption,
        id=message_id,
        outgoing=own,
        from_user=SimpleNamespace(is_self=False, id=42),
    )
    return SimpleNamespace(
        message=message, chat_id=chat_id, rule_id="general", type="message"
    )


@pytest.mark.asyncio
async def test_multi_destination_modes(tmp_path):
    ctx = Context(tmp_path)
    params = {
        "targets": [
            {"chat_id": "@backup", "mode": "forward"},
            {"chat_id": "@archive", "mode": "copy"},
            {"chat_id": "@text", "mode": "text"},
            {
                "chat_id": "@bot", "mode": "extract",
                "pattern": r"(https?://115cdn\.com/s/[^\s]+)", "dedup": "content",
            },
            {
                "chat_id": "@notify", "mode": "template",
                "template": "from={chat_id} content={text} note={note}",
            },
        ]
    }
    incoming = event()
    assert await relay_message(incoming, ctx, params) == "continue"
    assert ctx.client.forwarded == [("@backup", -100, 10)]
    assert ctx.client.copied == [("@archive", -100, 10)]
    assert ctx.worker.sent == [
        ("@text", incoming.message.text),
        ("@bot", "https://115cdn.com/s/ABC?password=999#"),
        ("@notify", f"from=-100 content={incoming.message.text} note=example"),
    ]
    assert await relay_message(incoming, ctx, params) == "continue"
    assert len(ctx.worker.sent) == 3


@pytest.mark.asyncio
async def test_content_deduplication_across_sources(tmp_path):
    ctx = Context(tmp_path)
    params = {
        "targets": [
            {
                "chat_id": "@bot", "mode": "extract",
                "pattern": r"(https?://115cdn\.com/s/[^\s]+)",
                "dedup": "content",
            }
        ]
    }
    assert await relay_message(event(), ctx, params) == "continue"
    assert await relay_message(
        event(chat_id=-200, message_id=22), ctx, params
    ) == "continue"
    assert len(ctx.worker.sent) == 1
    with sqlite3.connect(tmp_path / "message_relay.sqlite3") as db:
        assert db.execute(
            "SELECT COUNT(*) FROM relay_deliveries"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_message_dedup_allows_new_post(tmp_path):
    ctx = Context(tmp_path)
    params = {"targets": [{"chat_id": "@bot", "mode": "text"}]}
    assert await relay_message(event(), ctx, params) == "continue"
    assert await relay_message(event(message_id=11), ctx, params) == "continue"
    assert await relay_message(event(message_id=11), ctx, params) == "continue"
    assert len(ctx.worker.sent) == 2


@pytest.mark.asyncio
async def test_failed_delivery_retries_after_zero_cooldown(tmp_path):
    ctx = Context(tmp_path)
    ctx.worker.fail = True
    params = {
        "targets": [
            {"chat_id": "@bot", "mode": "text", "retry_after_seconds": 0}
        ]
    }
    assert await relay_message(event(), ctx, params) == "stop"
    ctx.worker.fail = False
    assert await relay_message(event(), ctx, params) == "continue"
    assert len(ctx.worker.sent) == 1
    with sqlite3.connect(tmp_path / "message_relay.sqlite3") as db:
        assert db.execute(
            "SELECT status, attempts FROM relay_deliveries"
        ).fetchone() == ("sent", 2)


@pytest.mark.asyncio
async def test_caption_named_group_and_multiple_matches(tmp_path):
    ctx = Context(tmp_path)
    incoming = event(text=None, caption="SKU A1 and B2")
    params = {
        "targets": [
            {
                "chat_id": "@list", "mode": "extract",
                "pattern": r"(?P<sku>[A-Z]\d)", "group": "sku",
                "all_matches": True,
            },
            {
                "chat_id": "@summary", "mode": "template",
                "pattern": r"(?P<sku>[A-Z]\d)",
                "template": "{sku}: {text} by {sender_id}",
            },
        ]
    }
    assert await relay_message(incoming, ctx, params) == "continue"
    assert ctx.worker.sent == [
        ("@list", "A1"),
        ("@list", "B2"),
        ("@summary", "A1: SKU A1 and B2 by 42"),
    ]


@pytest.mark.asyncio
async def test_forward_media_without_caption(tmp_path):
    ctx = Context(tmp_path)
    params = {"targets": [{"chat_id": "@archive", "mode": "forward"}]}
    assert await relay_message(event(text=None), ctx, params) == "continue"
    assert ctx.client.forwarded == [("@archive", -100, 10)]


@pytest.mark.asyncio
async def test_invalid_config_skips_delivery(tmp_path):
    ctx = Context(tmp_path)
    params = {
        "targets": [
            {"chat_id": "@bot", "mode": "extract", "pattern": "("},
            {"mode": "forward"},
        ]
    }
    assert await relay_message(event(), ctx, params) == "stop"
    assert ctx.worker.sent == []


@pytest.mark.asyncio
async def test_skip_own_message_and_same_chat(tmp_path):
    ctx = Context(tmp_path)
    params = {"targets": [{"chat_id": "@bot", "mode": "text"}]}
    assert await relay_message(event(own=True), ctx, params) == "continue"
    assert await relay_message(
        event(), ctx, {"targets": [{"chat_id": -100, "mode": "text"}]}
    ) == "stop"
    assert ctx.worker.sent == []


@pytest.mark.asyncio
async def test_dedup_off_still_records_each_attempt_for_history(tmp_path):
    ctx = Context(tmp_path)
    params = {
        "targets": [{"chat_id": "@bot", "mode": "text", "dedup": "off"}]
    }
    assert await relay_message(event(), ctx, params) == "continue"
    assert await relay_message(event(), ctx, params) == "continue"
    assert len(ctx.worker.sent) == 2
    ledger = RelayLedger(tmp_path)
    rows = ledger.history()["items"]
    assert len(rows) == 2
    assert all(row["dedup"] == "off" and row["status"] == "sent" for row in rows)
    assert rows[0]["fingerprint"] != rows[1]["fingerprint"]
    assert all(row["retryable"] is False for row in rows)


@pytest.mark.asyncio
async def test_failed_delivery_manual_retry_ignores_cooldown(tmp_path):
    ctx = Context(tmp_path)
    ctx.worker.fail = True
    params = {
        "targets": [{
            "chat_id": "@bot", "mode": "text", "retry_after_seconds": 3600
        }]
    }
    incoming = event(text="original")
    assert await relay_message(incoming, ctx, params) == "stop"
    ledger = RelayLedger(tmp_path)
    failed = ledger.history(status="failed")["items"][0]
    assert failed["retryable"]
    assert (failed["task_id"], failed["account_id"]) == (3, 4)
    assert (failed["handler_index"], failed["target_index"], failed["part_index"]) == (0, 0, 0)
    ctx.worker.fail = False
    ref = (
        failed["destination"], failed["fingerprint"],
        failed["target_index"], failed["part_index"], failed["payload_digest"],
    )
    assert await relay_message(incoming, ctx, params, retry_ref=ref) == "continue"
    sent = ledger.get(failed["destination"], failed["fingerprint"])
    assert (sent["status"], sent["attempts"]) == ("sent", 2)
    # Never send a successful entry a second time.
    assert await relay_message(incoming, ctx, params, retry_ref=ref) == "continue"
    assert len(ctx.worker.sent) == 1
    assert ledger.get(failed["destination"], failed["fingerprint"])["attempts"] == 2


@pytest.mark.asyncio
async def test_manual_retry_rejects_edited_payload(tmp_path):
    ctx = Context(tmp_path)
    ctx.worker.fail = True
    params = {"targets": [{"chat_id": "@bot", "mode": "text"}]}
    await relay_message(event(text="original"), ctx, params)
    ledger = RelayLedger(tmp_path)
    failed = ledger.history(status="failed")["items"][0]
    ctx.worker.fail = False
    await relay_message(
        event(text="changed"), ctx, params,
        retry_ref=(
            failed["destination"], failed["fingerprint"],
            0, 0, failed["payload_digest"],
        ),
    )
    assert ctx.worker.sent == []
    assert ledger.get(failed["destination"], failed["fingerprint"])["attempts"] == 1




def test_concurrent_reservation(tmp_path):
    first = RelayLedger(tmp_path)
    second = RelayLedger(tmp_path)
    digest = RelayLedger.hash_value("example")
    assert first.reserve("@bot", digest, "text", "-100", 10) == ("reserved", 1)
    assert second.reserve("@bot", digest, "text", "-100", 10) == (
        "inflight", None
    )
    first.finish("@bot", digest, 1, True)
    assert second.reserve("@bot", digest, "text", "-100", 10) == (
        "duplicate", None
    )


@pytest.mark.asyncio
async def test_content_dedup_does_not_drop_media_without_caption(tmp_path):
    ctx = Context(tmp_path)
    params = {
        "targets": [
            {"chat_id": "@archive", "mode": "forward", "dedup": "content"}
        ]
    }
    assert await relay_message(
        event(text=None, message_id=1), ctx, params
    ) == "continue"
    assert await relay_message(
        event(text=None, message_id=2), ctx, params
    ) == "continue"
    assert len(ctx.client.forwarded) == 2


@pytest.mark.asyncio
async def test_message_dedup_is_independent_between_rules(tmp_path):
    ctx = Context(tmp_path)
    params = {"targets": [{"chat_id": "@bot", "mode": "text"}]}
    first = event()
    second = event()
    second.rule_id = "separate_rule"
    assert await relay_message(first, ctx, params) == "continue"
    assert await relay_message(second, ctx, params) == "continue"
    assert len(ctx.worker.sent) == 2


@pytest.mark.asyncio
async def test_same_caption_different_photos_are_not_deduplicated(tmp_path):
    ctx = Context(tmp_path)
    params = {
        "targets": [
            {"chat_id": "@archive", "mode": "forward", "dedup": "content"},
        ]
    }
    first = event(text=None, caption="同一段图片说明", message_id=101)
    second = event(text=None, caption="同一段图片说明", message_id=102)
    first.message.photo = object()
    second.message.photo = object()
    assert await relay_message(first, ctx, params) == "continue"
    assert await relay_message(second, ctx, params) == "continue"
    assert ctx.client.forwarded == [
        ("@archive", -100, 101),
        ("@archive", -100, 102),
    ]


@pytest.mark.asyncio
async def test_regex_all_matches_is_capped_at_twenty(tmp_path):
    ctx = Context(tmp_path)
    params = {
        "targets": [{
            "chat_id": "@archive",
            "mode": "extract",
            "pattern": r"(X)",
            "all_matches": True,
            "dedup": "message",
        }]
    }
    assert await relay_message(event(text="X" * 1000), ctx, params) == "continue"
    assert len(ctx.worker.sent) == 20


def test_legacy_schema_migration_keeps_original_rows(tmp_path):
    old = tmp_path / "message_relay.sqlite3"
    with sqlite3.connect(old) as db:
        db.execute(
            """CREATE TABLE relay_deliveries (
                destination TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                action TEXT NOT NULL,
                source_chat TEXT NOT NULL,
                source_message INTEGER,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_error TEXT,
                PRIMARY KEY (destination, fingerprint)
            )"""
        )
        db.execute(
            "INSERT INTO relay_deliveries VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("@target", "f" * 64, "forward", "-100", 8,
             "failed", 1, 101, 102, "RPCError"),
        )
    ledger = RelayLedger(tmp_path)
    assert ledger.history()["total"] == 1
    entry = ledger.get("@target", "f" * 64)
    assert (entry["status"], entry["attempts"], entry["last_error"]) == (
        "failed", 1, "RPCError"
    )
    assert entry["retryable"] is False
    assert entry["task_id"] is None
    # Reopening must be idempotent.
    assert RelayLedger(tmp_path).history()["total"] == 1


def test_delivery_stats_and_bounded_query(tmp_path):
    ledger = RelayLedger(tmp_path)
    key = "f" * 64
    assert ledger.reserve("@one", key, "forward", "-100", 2) == ("reserved", 1)
    ledger.finish("@one", key, 1, True)
    key2 = "a" * 64
    assert ledger.reserve("@two", key2, "copy", "-100", 3) == ("reserved", 1)
    ledger.finish("@two", key2, 1, False, "SendFailed")
    status = ledger.stats()
    assert (status["sent"], status["failed"], status["total"]) == (1, 1, 2)
    assert status["success_rate"] == 50.0
    assert sum(d["sent"] for d in status["days"]) == 1
    assert ledger.history(page=1, page_size=1)["total"] == 2
    assert len(ledger.history(page=2, page_size=1)["items"]) == 1
    assert len(ledger.history(status="failed")["items"]) == 1
    with pytest.raises(ValueError):
        ledger.history(page_size=101)


def test_force_retry_cannot_resend_success_or_active_lease(tmp_path):
    ledger = RelayLedger(tmp_path)
    fingerprint = "9" * 64
    assert ledger.reserve("@one", fingerprint, "text", "-1", 2) == ("reserved", 1)
    assert ledger.reserve(
        "@one", fingerprint, "text", "-1", 2, force_failed=True
    ) == ("inflight", None)
    ledger.finish("@one", fingerprint, 1, True)
    assert ledger.reserve(
        "@one", fingerprint, "text", "-1", 2, force_failed=True
    ) == ("duplicate", None)
    assert ledger.reserve(
        "@missing", "a" * 64, "text", "-1", 2, force_failed=True
    ) == ("not_found", None)
