"""P7 Bot outcome and media-album idempotency regressions (offline)."""

import sqlite3
import time
from types import SimpleNamespace

import pytest

from tg_signer.automation.bot_results import BotResultMixin
from tg_signer.automation.message_relay import relay_message
from tg_signer.automation.relay_ledger import RelayLedger


def test_bot_receipts_exact_reply_and_separate_from_telegram_send(tmp_path):
    ledger = RelayLedger(tmp_path)
    fingerprint = "a" * 64
    assert ledger.reserve(
        "@115bot", fingerprint, "extract", "-100", 123,
        account_id=4, task_id=3,
    ) == ("reserved", 1)
    ledger.finish("@115bot", fingerprint, 1, True, destination_message=77)
    assert ledger.track_bot_send(
        destination="@115bot", fingerprint=fingerprint,
        account_id=4, task_id=3, bot_chat=999, sent_message=77,
        success_pattern=r"转存成功", failure_pattern=r"转存失败",
        timeout_minutes=120,
    )
    assert not ledger.track_bot_send(
        destination="@115bot", fingerprint=fingerprint,
        account_id=4, task_id=3, bot_chat=999, sent_message=77,
        success_pattern=r"转存成功", failure_pattern=r"转存失败",
        timeout_minutes=120,
    )
    assert ledger.observe_bot_reply(
        account_id=5, bot_chat=999, reply_to_message=77,
        bot_message=81, text="转存成功", 
    ) is None
    assert ledger.observe_bot_reply(
        account_id=4, bot_chat=1000, reply_to_message=77,
        bot_message=81, text="转存成功",
    ) is None
    assert ledger.observe_bot_reply(
        account_id=4, bot_chat=999, reply_to_message=78,
        bot_message=81, text="转存成功",
    ) is None
    row = ledger.observe_bot_reply(
        account_id=4, bot_chat=999, reply_to_message=77,
        bot_message=81, text="任务处理中 https://115cdn.com/s/secret-token",
    )
    assert row["status"] == "unclassified"
    result = ledger.observe_bot_reply(
        account_id=4, bot_chat=999, reply_to_message=77,
        bot_message=82, text="转存成功 https://115cdn.com/s/secret-token",
    )
    assert result["status"] == "succeeded"
    assert ledger.observe_bot_reply(
        account_id=4, bot_chat=999, reply_to_message=77,
        bot_message=83, text="转存失败",
    ) is None
    records = ledger.bot_results()
    assert records["total"] == 1
    assert records["items"][0]["status"] == "succeeded"
    assert ledger.get("@115bot", fingerprint)["status"] == "sent"
    assert b"secret-token" not in (tmp_path / "message_relay.sqlite3").read_bytes()
    with sqlite3.connect(tmp_path / "message_relay.sqlite3") as db:
        db.execute(
            "UPDATE relay_bot_results SET status='pending',deadline=?",
            (int(time.time()) - 5,),
        )
    assert ledger.bot_results(status="expired")["total"] == 1


def test_receipt_pattern_failure_priority_and_immutable_correlation(tmp_path):
    ledger = RelayLedger(tmp_path)
    key = "b" * 64
    assert ledger.reserve("@bot", key, "text", "-100", 1) == ("reserved", 1)
    assert not ledger.track_bot_send(
        destination="@bot", fingerprint=key,
        account_id=4, task_id=3, bot_chat=88, sent_message=5,
        success_pattern="成功", failure_pattern="失败", timeout_minutes=20,
    )
    ledger.finish("@bot", key, 1, True, destination_message=5)
    assert ledger.track_bot_send(
        destination="@bot", fingerprint=key,
        account_id=4, task_id=3, bot_chat=88, sent_message=5,
        success_pattern="完成|成功", failure_pattern="失败", timeout_minutes=20,
    )
    result = ledger.observe_bot_reply(
        account_id=4, bot_chat=88, reply_to_message=5,
        bot_message=8, text="失败，但成功",
    )
    assert result["status"] == "failed"
    assert ledger.get("@bot", key)["status"] == "sent"


class AlbumClient:
    def __init__(self, members):
        self.members = members
        self.forwards = []
        self.copies = []

    async def get_media_group(self, chat_id, message_id):
        return list(self.members)

    async def forward_messages(self, dest, chat_id, message_ids):
        self.forwards.append((dest, chat_id, message_ids))
        return [
            SimpleNamespace(id=200+i, chat=SimpleNamespace(id=888))
            for i in range(len(message_ids))
        ]

    async def copy_media_group(self, dest, chat_id, message_id):
        self.copies.append((dest, chat_id, message_id))
        return [
            SimpleNamespace(id=300+i, chat=SimpleNamespace(id=999))
            for i in range(len(self.members))
        ]


class Context:
    def __init__(self, directory, members):
        self.workdir = directory
        self.worker = SimpleNamespace(task_name="3", account_id=4, config={"rules":[]})
        self.client = AlbumClient(members)
        self.handler_index = 0
        self.vars = {}
        self.logs = []

    def log(self, value, level="INFO"):
        self.logs.append((level, value))


def media_message(message_id, group_id="album-xyz"):
    return SimpleNamespace(
        id=message_id, media_group_id=group_id,
        text=None, caption="media item",
        chat=SimpleNamespace(id=-100), from_user=SimpleNamespace(is_self=False,id=42),
        outgoing=False, photo=SimpleNamespace(file_unique_id="photo-"+str(message_id)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["forward", "copy"])
async def test_album_is_one_delivery_even_with_multiple_callbacks(
    monkeypatch, tmp_path, mode,
):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("tg_signer.automation.media_groups.asyncio.sleep", no_sleep)
    members = [media_message(10), media_message(11), media_message(12)]
    ctx = Context(tmp_path, members)
    for member in members:
        event = SimpleNamespace(
            chat_id=-100, rule_id="albumrule", message=member, type="message",
        )
        outcome = await relay_message(event, ctx, {
            "targets": [{
                "chat_id": "@album", "mode": mode,
                "album_mode": "album", "dedup": "off",
            }]
        })
        assert outcome == "continue"
    assert len(ctx.client.forwards if mode == "forward" else ctx.client.copies) == 1
    ledger = RelayLedger(tmp_path)
    items = ledger.history()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "sent"
    assert items[0]["action"] == "album_"+mode
    assert items[0]["media_count"] == 3
    assert items[0]["media_group_key"] == "album-xyz"
    assert items[0]["retryable"] is False
    assert ledger.album_stats()["sent"] == 1


@pytest.mark.asyncio
async def test_incomplete_album_never_sends_single_media(monkeypatch, tmp_path):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("tg_signer.automation.media_groups.asyncio.sleep", no_sleep)
    ctx = Context(tmp_path, [media_message(10)])
    e = SimpleNamespace(chat_id=-100, rule_id="a", message=media_message(10))
    assert await relay_message(e, ctx, {
        "targets": [{"chat_id": "@album", "mode": "forward", "album_mode": "album"}]
    }) == "stop"
    assert not ctx.client.forwards
    assert not RelayLedger(tmp_path).history()["items"]


@pytest.mark.asyncio
async def test_album_partial_rpc_response_is_uncertain(monkeypatch, tmp_path):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("tg_signer.automation.media_groups.asyncio.sleep", no_sleep)
    members = [media_message(10), media_message(11)]
    ctx = Context(tmp_path, members)

    async def incomplete(*args):
        return [SimpleNamespace(id=800)]

    ctx.client.forward_messages = incomplete
    e = SimpleNamespace(chat_id=-100, rule_id="a", message=members[0])
    assert await relay_message(e, ctx, {
        "targets": [{"chat_id": "@album", "mode": "forward", "album_mode": "album"}]
    }) == "stop"
    item = RelayLedger(tmp_path).history()["items"][0]
    assert item["status"] == "uncertain"
    assert item["retryable"] is False
