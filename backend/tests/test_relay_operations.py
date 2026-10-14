"""P5: incident summary, export safeguards, and live listener visibility."""
import csv
import io

from backend.app import relay_history, relay_operations
from backend.app.db import Account, Session, Task
from tg_signer.automation.relay_ledger import RelayLedger


def test_operations_and_export_require_admin(client):
    headers = {"Cookie": "tg_dashboard=invalid"}
    assert client.get("/api/relay/operations", headers=headers).status_code == 401
    assert client.get("/api/relay/export", headers=headers).status_code == 401


def test_fault_counts_and_csv_escaping(admin, monkeypatch, tmp_path):
    ledger = RelayLedger(tmp_path)
    monkeypatch.setattr(relay_operations, "get_ledger", lambda: ledger)
    bad = "f" * 64
    good = "e" * 64
    assert ledger.reserve("=DANGEROUS()", bad, "text", "-100", 7) == ("reserved", 1)
    ledger.finish("=DANGEROUS()", bad, 1, False, "FloodWait", failure_kind="transient")
    assert ledger.reserve("@safe", good, "text", "-100", 8) == ("reserved", 1)
    ledger.finish("@safe", good, 1, True)
    snapshot = admin.get("/api/relay/operations")
    assert snapshot.status_code == 200, snapshot.text
    body = snapshot.json()
    assert body["faults"]["failed"] == 1
    assert body["faults"]["sent"] == 1
    assert body["faults"]["total"] == 1
    assert isinstance(body["listeners"], list)
    assert body["retries"]["scheduled"] == 1

    response = admin.get("/api/relay/export", params={"status": "failed"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["x-export-count"] == "1"
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert len(rows) == 1
    assert rows[0]["destination"] == "'=DANGEROUS()"
    assert "payload" not in rows[0]
    assert "retry_claim" not in rows[0]
    assert admin.get("/api/relay/export", params={"limit": 10001}).status_code == 422
    assert admin.get("/api/relay/export", params={"from_ts": 20, "to_ts": 10}).status_code == 422
    assert admin.get("/api/relay/export", params={"status": "oops"}).status_code == 422


def test_listener_does_not_conflate_enabled_with_ready(admin, account):
    with Session() as db:
        db.add(Task(name="pending P5 listener", account_id=account, kind="automation",
                    enabled=True, config={"rules": []}))
        db.commit()
    response = admin.get("/api/relay/operations")
    assert response.status_code == 200
    matches = [x for x in response.json()["listeners"] if x["task_name"] == "pending P5 listener"]
    assert len(matches) == 1
    assert matches[0]["state"] != "listening"
