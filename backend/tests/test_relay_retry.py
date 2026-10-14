"""P4 policy, review authorization and source-verified bounded retry execution."""

import json
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from tg_signer.automation.delivery_outcome import configuration_digest, source_snapshot_digest
from tg_signer.automation.message_relay import _fingerprint
from tg_signer.automation.models import Event
from tg_signer.automation.relay_ledger import RelayLedger
from tg_signer.config import AutomationConfig

from backend.app import relay_history
from backend.app.db import Session, Task, set_setting


def test_review_and_policy_require_auth_and_explicit_confirmation(admin, monkeypatch, tmp_path):
    from backend.app import relay_retry

    ledger = RelayLedger(tmp_path)
    monkeypatch.setattr(relay_history, "get_ledger", lambda: ledger)
    monkeypatch.setattr(relay_retry, "get_ledger", lambda: ledger)
    bad = {"Cookie": "tg_dashboard=bad"}
    assert admin.get("/api/relay/retry-policy", headers=bad).status_code == 401
    assert admin.put("/api/relay/retry-policy", json={}, headers=bad).status_code == 401
    assert admin.post("/api/relay/resolve", json={}, headers=bad).status_code == 401
    assert admin.post("/api/relay/retry-control", json={}, headers=bad).status_code == 401
    assert admin.get("/api/relay/retry-policy").json()["policy"]["enabled"] is False
    assert admin.put("/api/relay/retry-policy", json={"max_attempts": 21}).status_code == 422
    assert (
        admin.put("/api/relay/retry-policy", json={"base_delay": 300, "max_delay": 60}).status_code
        == 422
    )
    ref = "a" * 64
    ledger.reserve("@target", ref, "text", "-100", 7)
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE relay_deliveries SET lease_until=1")
    ledger.recover_stale()
    body = {"destination": "@target", "fingerprint": ref, "attempt": 1, "decision": "sent"}
    assert admin.post("/api/relay/resolve", json=body).status_code == 422
    body["confirmation"] = "checked_destination"
    assert admin.post("/api/relay/resolve", json=body).status_code == 200
    assert ledger.get("@target", ref)["status"] == "sent"
    assert admin.post("/api/relay/resolve", json=body).status_code == 409


@pytest.fixture
def queued(account, tmp_path, monkeypatch):
    from backend.app import relay_retry

    ledger = RelayLedger(tmp_path)
    monkeypatch.setattr(relay_history, "get_ledger", lambda: ledger)
    monkeypatch.setattr(relay_retry, "get_ledger", lambda: ledger)
    config = AutomationConfig.model_validate(
        {
            "rules": [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "message", "params": {"chat_ids": [-100]}}],
                    "handlers": [
                        {
                            "handler": "relay_message",
                            "params": {
                                "targets": [
                                    {"chat_id": "@target", "mode": "text", "retry_after_seconds": 0}
                                ]
                            },
                        }
                    ],
                }
            ]
        }
    )
    with Session() as db:
        task = Task(
            name="recovery",
            account_id=account,
            kind="automation",
            enabled=True,
            config=config.model_dump(mode="json"),
        )
        db.add(task)
        db.commit()
        task_id = task.id
    msg = SimpleNamespace(
        id=7,
        text="original",
        caption=None,
        chat=SimpleNamespace(id=-100),
        outgoing=False,
        from_user=SimpleNamespace(id=8, is_self=False),
    )
    event = Event(
        type="message",
        chat_id=-100,
        message=msg,
        rule_id="r",
        trigger_id="t",
        now=datetime.now(timezone.utc),
    )
    ref = _fingerprint("text", "message", event, msg, msg.text, 0)
    ledger.reserve(
        "@target",
        ref,
        "text",
        "-100",
        7,
        retry_after_seconds=0,
        account_id=account,
        task_id=task_id,
        rule_id="r",
        handler_index=0,
        target_index=0,
        part_index=0,
        dedup="message",
        payload_digest=RelayLedger.hash_value("text:" + msg.text),
        config_digest=configuration_digest(config),
        source_digest=source_snapshot_digest(msg),
    )
    ledger.finish("@target", ref, 1, False, "FloodWait", failure_kind="transient")
    with sqlite3.connect(ledger.path) as db:
        db.execute(
            "UPDATE relay_deliveries SET next_retry_at=1,updated_at=?", (int(time.time()) - 100,)
        )
    sent = []

    class Worker:
        def __init__(self):
            self.app = self
            self.task_name = str(task_id)
            self.account_id = account
            self.workdir = tmp_path
            self.state = SimpleNamespace(get_rule_vars=lambda name: {})

        async def get_messages(self, source, id_):
            return msg

        async def send_message(self, destination, text):
            sent.append((destination, text))
            return SimpleNamespace(id=99)

        def _match_message_trigger(self, trigger, message):
            return True

        def _match_filter(self, filters, message):
            return True

    async def worker(*args):
        return Worker()

    monkeypatch.setattr(relay_history.telegram, "worker", worker)
    set_setting("relay_retry_policy", "{}")
    yield SimpleNamespace(
        ledger=ledger, sent=sent, message=msg, task_id=task_id, ref=ref, worker_cls=Worker
    )
    set_setting("relay_retry_policy", "{}")


@pytest.mark.asyncio
async def test_opt_in_queue_replays_once_and_stops_success(admin, queued):
    from backend.app.relay_retry import process_retries

    await process_retries()
    assert queued.sent == []
    set_setting("relay_retry_policy", json.dumps({"enabled": True}))
    await process_retries()
    await process_retries()
    assert queued.sent == [("@target", "original")]
    assert queued.ledger.get("@target", queued.ref)["auto_attempts"] == 1
    assert queued.ledger.get("@target", queued.ref)["status"] == "sent"


@pytest.mark.asyncio
async def test_changed_task_or_source_blocks_auto_send(admin, queued):
    from backend.app.relay_retry import process_retries

    set_setting("relay_retry_policy", json.dumps({"enabled": True}))
    queued.message.text = "edited"
    await process_retries()
    assert queued.sent == []
    row = queued.ledger.get("@target", queued.ref)
    assert row["retry_status"] == "blocked"
    assert row["next_retry_at"] is None


@pytest.mark.asyncio
async def test_source_edit_blocks_extract_even_when_output_is_unchanged(admin, queued):
    from backend.app.relay_retry import process_retries

    with Session() as db:
        task = db.get(Task, queued.task_id)
        value = json.loads(json.dumps(task.config))
        target = value["rules"][0]["handlers"][0]["params"]["targets"][0]
        target.update(mode="extract", pattern="(original)")
        task.config = value
        db.commit()
    config = AutomationConfig.model_validate(value)
    with sqlite3.connect(queued.ledger.path) as db:
        db.execute(
            "UPDATE relay_deliveries SET action='extract',payload_digest=?,config_digest=?",
            (RelayLedger.hash_value("extract:original"), configuration_digest(config)),
        )
    queued.message.text = "original plus edited context"
    set_setting("relay_retry_policy", json.dumps({"enabled": True}))
    await process_retries()
    assert queued.sent == []
    assert queued.ledger.get("@target", queued.ref)["retry_status"] == "blocked"


@pytest.mark.asyncio
async def test_changed_config_blocks_even_when_same_output(admin, queued):
    from backend.app.relay_retry import process_retries

    with Session() as db:
        task = db.get(Task, queued.task_id)
        value = json.loads(json.dumps(task.config))
        value["rules"][0]["handlers"][0]["params"]["ignore_self"] = False
        task.config = value
        db.commit()
    set_setting("relay_retry_policy", json.dumps({"enabled": True}))
    await process_retries()
    assert queued.sent == []
    assert queued.ledger.get("@target", queued.ref)["retry_status"] == "blocked"


@pytest.mark.asyncio
async def test_task_disabled_while_waiting_for_send_lock_is_not_sent(admin, queued):
    from backend.app.relay_retry import process_retries

    async def wrapper(self, operation, call, **kwargs):
        with Session() as db:
            task = db.get(Task, queued.task_id)
            task.enabled = False
            db.commit()
        return await call()

    queued.worker_cls._call_telegram_api = wrapper
    set_setting("relay_retry_policy", json.dumps({"enabled": True}))
    await process_retries()
    assert queued.sent == []
    assert queued.ledger.get("@target", queued.ref)["retry_status"] == "blocked"
