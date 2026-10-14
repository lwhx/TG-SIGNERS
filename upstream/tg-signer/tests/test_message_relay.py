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
async def test_dedup_off_does_not_touch_database(tmp_path):
    ctx = Context(tmp_path)
    params = {
        "targets": [{"chat_id": "@bot", "mode": "text", "dedup": "off"}]
    }
    assert await relay_message(event(), ctx, params) == "continue"
    assert await relay_message(event(), ctx, params) == "continue"
    assert len(ctx.worker.sent) == 2
    assert not (tmp_path / "message_relay.sqlite3").exists()


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
