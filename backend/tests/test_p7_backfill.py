"""P7 authenticated backfill preflight and bounded worker execution tests."""

import time
from types import SimpleNamespace

from backend.app import relay_backfill
from backend.app.db import RelayBackfill, Session, Task
from backend.tests.test_api import task_payload
from tg_signer.automation.relay_ledger import RelayLedger


def relay_config(*, unsafe=False, dedup="message"):
    handlers = [{"handler": "relay_message", "params": {
        "ignore_self": True,
        "targets": [{"chat_id": "@archive", "mode": "text", "dedup": dedup}],
    }}]
    if unsafe:
        handlers.append({"handler": "send_text", "params": {"text": "SIDE EFFECT"}})
    return {"rules": [{
        "id": "historical",
        "enabled": True,
        "triggers": [{"type": "message", "params": {"chat_ids": ["@source"]}}],
        "filters": {"text_rule": "all"},
        "handlers": handlers,
        "vars": {},
    }]}


def create_enabled_task(admin, account, config):
    payload = task_payload(
        account, kind="automation", name="P7 test",
        config=config, enabled=False,
    )
    created = admin.post("/api/tasks", json=payload)
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    with Session() as db:
        task = db.get(Task, task_id)
        task.enabled = True
        db.commit()
    return task_id


def cleanup_task(admin, task_id):
    with Session() as db:
        job_ids = [row.id for row in db.query(RelayBackfill).filter_by(task_id=task_id).all()]
        for job_id in job_ids:
            db.delete(db.get(RelayBackfill, job_id))
        task = db.get(Task, task_id)
        if task:
            db.delete(task)
        db.commit()


def test_backfill_preview_requires_explicit_authorized_source(admin, account, client):
    task_id = create_enabled_task(admin, account, relay_config())
    payload = {"task_id": task_id, "source_chat": "@source", "limit": 5}
    try:
        assert client.post(
            "/api/relay/backfill/preview", json=payload,
            headers={"Cookie": "tg_dashboard=invalid"},
        ).status_code == 401
        preview = admin.post("/api/relay/backfill/preview", json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["rules"] == ["historical"]
        assert preview.json()["target_count"] == 1
        assert admin.post("/api/relay/backfill/start", json=payload).status_code == 400
        assert admin.post("/api/relay/backfill/preview", json={
            **payload, "source_chat": "@not-listed"
        }).status_code == 422
        assert admin.post("/api/relay/backfill/preview", json={
            **payload, "limit": 51
        }).status_code == 422
        assert admin.post("/api/relay/backfill/preview", json={
            **payload, "before_id": -1
        }).status_code == 422
        assert admin.get("/api/relay/media-groups/stats").status_code == 200
        assert admin.get("/api/relay/bot-results").status_code == 200
        assert admin.get("/api/relay/bot-results?page_size=101").status_code == 422
    finally:
        cleanup_task(admin, task_id)


def test_backfill_disallows_side_effects_and_no_dedup(admin, account):
    for config in (relay_config(unsafe=True), relay_config(dedup="off")):
        task_id = create_enabled_task(admin, account, config)
        try:
            result = admin.post("/api/relay/backfill/preview", json={
                "task_id": task_id, "source_chat": "@source",
            })
            assert result.status_code == 422
        finally:
            cleanup_task(admin, task_id)


def test_manual_backfill_sends_once_and_persists_cursor(admin, account, tmp_path, monkeypatch):
    task_id = create_enabled_task(admin, account, relay_config())
    content = SimpleNamespace(
        id=14, text="Historical 115 link", caption=None, empty=False,
        outgoing=False, media_group_id=None,
        chat=SimpleNamespace(id=-100, username="source"),
        from_user=SimpleNamespace(is_self=False, id=22),
    )

    class Client:
        relay_single_attempt = True

        def __init__(self):
            self.sent = []

        async def get_chat_history(self, chat, limit=20, offset_id=0):
            assert chat == "@source"
            if offset_id in (0, 20):
                yield content

        async def send_message(self, destination, text):
            self.sent.append((destination, text))
            return SimpleNamespace(
                id=100+len(self.sent), chat=SimpleNamespace(id=999)
            )

    tg_client = Client()

    class Worker:
        def __init__(self):
            self.app = tg_client
            self.task_name = str(task_id)
            self.workdir = tmp_path
            self.state = SimpleNamespace(get_rule_vars=lambda _id: {})

        def _match_message_trigger(self, trigger, msg):
            return True

        def _match_filter(self, filters, msg):
            return True

    async def get_worker(account_id, cls, selected_task):
        assert account_id == account
        assert selected_task == task_id
        return Worker()

    monkeypatch.setattr(relay_backfill.telegram, "worker", get_worker)
    payload = {
        "task_id": task_id, "source_chat": "@source", "limit": 1,
        "confirmation": "BACKFILL",
    }
    try:
        first = admin.post("/api/relay/backfill/start", json=payload)
        assert first.status_code == 202, first.text
        first_id = first.json()["id"]
        for _ in range(70):
            rows = admin.get("/api/relay/backfill/jobs", params={"task_id": task_id}).json()["items"]
            if rows and rows[0]["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        assert rows[0]["status"] == "completed", rows[0]
        assert rows[0]["scanned"] == 1
        assert rows[0]["matched"] == 1
        assert rows[0]["next_before_id"] == 14
        assert tg_client.sent == [("@archive", "Historical 115 link")]

        second = admin.post("/api/relay/backfill/start", json=payload)
        assert second.status_code == 202, second.text
        for _ in range(70):
            rows = admin.get("/api/relay/backfill/jobs", params={"task_id": task_id}).json()["items"]
            if rows and rows[0]["id"] == second.json()["id"] and rows[0]["status"] in {"completed","failed"}:
                break
            time.sleep(0.05)
        assert rows[0]["status"] == "completed", rows[0]
        assert len(tg_client.sent) == 1
        assert RelayLedger(tmp_path).history()["total"] == 1
        assert admin.get("/api/relay/backfill/jobs?limit=51").status_code == 422
    finally:
        cleanup_task(admin, task_id)


def test_interrupted_jobs_are_not_automatically_restarted(admin, account):
    task_id = create_enabled_task(admin, account, relay_config())
    try:
        with Session() as db:
            db.add(RelayBackfill(
                task_id=task_id, account_id=account, source_chat="@source",
                batch_size=10, status="running",
            ))
            db.commit()
        relay_backfill.recover_backfills()
        jobs = admin.get("/api/relay/backfill/jobs", params={"task_id":task_id}).json()["items"]
        assert jobs[0]["status"] == "interrupted"
        assert jobs[0]["last_error"] == "ServiceRestarted"
    finally:
        cleanup_task(admin, task_id)
