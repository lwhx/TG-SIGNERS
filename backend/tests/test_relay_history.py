"""Authenticated history/stats and exact-source manual replay API tests."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tg_signer.automation.message_relay import _fingerprint
from tg_signer.automation.models import Event
from tg_signer.automation.relay_ledger import RelayLedger

from backend.app import relay_history as handler
from backend.app.db import Session, Task


def test_history_requires_authentication(client: TestClient, monkeypatch, tmp_path):
    ledger = RelayLedger(tmp_path)
    monkeypatch.setattr(handler, "get_ledger", lambda: ledger)
    bad = {"Cookie": "tg_dashboard=bad-token"}
    assert client.get("/api/relay/history", headers=bad).status_code == 401
    assert client.get("/api/relay/stats", headers=bad).status_code == 401
    assert client.post("/api/relay/retry", json={
        "destination": "@one", "fingerprint": "a" * 64
    }, headers=bad).status_code == 401


def test_admin_history_stats_and_validation(admin: TestClient, monkeypatch, tmp_path):
    ledger = RelayLedger(tmp_path)
    monkeypatch.setattr(handler, "get_ledger", lambda: ledger)
    assert ledger.reserve("@one", "1" * 64, "forward", "-100", 5) == ("reserved", 1)
    ledger.finish("@one", "1" * 64, 1, False, "TestSendError")
    response = admin.get("/api/relay/history", params={"status": "failed"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    row = response.json()["items"][0]
    assert row["retryable"] is False  # legacy or missing linkage
    assert "payload" not in row and "message_text" not in row
    stats = admin.get("/api/relay/stats")
    assert stats.status_code == 200
    assert stats.json()["failed"] == 1
    assert len(stats.json()["days"]) == 7
    assert admin.get("/api/relay/history", params={"page_size": 101}).status_code == 422
    assert admin.get("/api/relay/history", params={"status": "invalid"}).status_code == 422
    retry = admin.post("/api/relay/retry", json={
        "destination": "@one", "fingerprint": "1" * 64,
    })
    assert retry.status_code == 409
    assert "缺少账号" in retry.json()["detail"]


@pytest.mark.usefixtures("admin")
def test_exact_source_replay_does_not_duplicate(admin, account, monkeypatch, tmp_path):
    ledger = RelayLedger(tmp_path)
    monkeypatch.setattr(handler, "get_ledger", lambda: ledger)

    target = {"chat_id": "@target", "mode": "text", "dedup": "message"}
    payload = {
        "rules": [{
            "id": "relay",
            "enabled": True,
            "triggers": [{"type": "message", "params": {"chat_ids": [-100]}}],
            "filters": {"text_rule": "all"},
            "handlers": [{"handler": "relay_message", "params": {
                "ignore_self": True, "targets": [target],
            }}],
            "vars": {},
        }]
    }
    with Session() as db:
        task = Task(name="API retry test", account_id=account,
                    kind="automation", enabled=True, config=payload)
        db.add(task)
        db.commit()
        task_id = task.id

    message = SimpleNamespace(
        id=123, text="hello relay history", caption=None, outgoing=False,
        from_user=SimpleNamespace(id=8, is_self=False),
        chat=SimpleNamespace(id=-100, username="source"),
    )
    event = Event(
        type="message", chat_id=-100, message=message,
        now=datetime.now(timezone.utc), trigger_id="t1", rule_id="relay",
    )
    fingerprint = _fingerprint("text", "message", event, message, message.text, 0)
    signature = RelayLedger.hash_value("text:" + message.text)
    assert ledger.reserve(
        "@target", fingerprint, "text", "-100", 123,
        account_id=account, task_id=task_id, rule_id="relay",
        handler_index=0, target_index=0, part_index=0,
        dedup="message", payload_digest=signature,
    ) == ("reserved", 1)
    ledger.finish("@target", fingerprint, 1, False, "FloodWait")

    calls = []

    class StubWorker:
        def __init__(self):
            self.app = self
            self.workdir = tmp_path
            self.task_name = str(task_id)
            self.account_id = account
            self.state = SimpleNamespace(get_rule_vars=lambda name: {})

        async def get_messages(self, source, message_id):
            assert source == -100 and message_id == 123
            return message

        async def send_message(self, destination, text):
            calls.append((destination, text))

        def _match_message_trigger(self, trigger, msg):
            return msg.chat.id in trigger.params.chat_ids

        def _match_filter(self, filter_cfg, msg):
            return True

    async def fake_worker(account_id, cls, id_):
        assert account_id == account and id_ == task_id
        return StubWorker()

    monkeypatch.setattr(handler.telegram, "worker", fake_worker)
    body = {"destination": "@target", "fingerprint": fingerprint}
    response = admin.post("/api/relay/retry", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert calls == [("@target", "hello relay history")]
    assert ledger.get("@target", fingerprint)["attempts"] == 2
    assert admin.post("/api/relay/retry", json=body).status_code == 409
    assert calls == [("@target", "hello relay history")]
