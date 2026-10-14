"""P3 policy, safe bounded cleanup and nonblocking alert delivery."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from tg_signer.automation.relay_ledger import RelayLedger

from backend.app.db import Account, Run, Session, Task, set_setting


def test_policy_requires_admin_and_validates(admin):
    bad = {"Cookie": "tg_dashboard=invalid"}
    for path in ["/api/maintenance", "/api/maintenance/preview"]:
        assert admin.get(path, headers=bad).status_code == 401
    assert admin.post("/api/maintenance/cleanup", headers=bad).status_code == 401
    assert admin.put("/api/maintenance/policy", json={}, headers=bad).status_code == 401
    assert admin.get("/api/maintenance").json()["policy"]["enabled"] is False
    assert admin.put("/api/maintenance/policy", json={"run_days": 0}).status_code == 422
    assert admin.put("/api/maintenance/policy", json={"relay_sent_days": 1}).status_code == 422


def test_cleanup_default_disabled_preserves_accounts_tasks_and_running(
    admin, account, monkeypatch, tmp_path
):
    from backend.app import maintenance

    ledger = RelayLedger(tmp_path)
    monkeypatch.setattr(maintenance, "get_ledger", lambda: ledger)
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    with Session() as db:
        task = Task(name="preserve", account_id=account, config={})
        db.add(task)
        db.flush()
        for state in ["success", "running", "failed"]:
            db.add(
                Run(
                    task_id=task.id,
                    task_name="old",
                    account_id=account,
                    account_name="test",
                    started_at=old,
                    finished_at=None if state == "running" else old,
                    status=state,
                )
            )
        db.commit()
        task_id = task.id
    set_setting("maintenance_policy", "{}")
    assert admin.post("/api/maintenance/cleanup").status_code == 409
    result = admin.put("/api/maintenance/policy", json={"enabled": True})
    assert result.status_code == 200
    assert admin.get("/api/maintenance/preview").json()["runs"] == 2
    assert admin.post("/api/maintenance/cleanup").json()["runs"] == 2
    with Session() as db:
        assert db.get(Account, account) is not None
        assert db.get(Task, task_id) is not None
        assert [r.status for r in db.query(Run).all()] == ["running"]
    set_setting("maintenance_policy", "{}")


@pytest.mark.asyncio
async def test_alert_summary_dedup_failure_isolation_and_opt_out(monkeypatch, tmp_path):
    from backend.app import maintenance
    from backend.app.security import encrypt

    ledger = RelayLedger(tmp_path)
    monkeypatch.setattr(maintenance, "get_ledger", lambda: ledger)
    calls = []

    async def sender(title, body):
        calls.append((title, body))
        return [{"ok": True}]

    monkeypatch.setattr(maintenance, "notify", sender)
    set_setting("notifications", encrypt('[{"type":"webhook","enabled":true}]'))
    set_setting("maintenance_policy", "{}")

    for i in range(3):
        ledger.reserve("@private", str(i), "text", "-100", i, task_id=8, account_id=9)
        ledger.finish("@private", str(i), 1, False, "FloodWait")
    await maintenance.check_relay_alerts()
    await maintenance.check_relay_alerts()
    assert len(calls) == 1
    assert "3" in calls[0][1] and "FloodWait" in calls[0][1]
    assert "@private" not in calls[0][1]
    set_setting("maintenance_policy", json.dumps({"alert_enabled": False}))
    ledger.reserve("@private", "new", "text", "-100", 4)
    ledger.finish("@private", "new", 1, False, "RuntimeError")
    await maintenance.check_relay_alerts()
    assert len(calls) == 1
    set_setting("maintenance_policy", "{}")
    set_setting("notifications", "")


def test_overview_aggregates_by_timezone_without_raw_responses(admin, account):
    with Session() as db:
        db.add(
            Run(
                task_name="overview",
                account_name="test",
                account_id=account,
                status="success",
                response="private response" * 10000,
            )
        )
        db.commit()
    result = admin.get("/api/overview").json()
    assert result["today_runs"] >= 1
    assert sum(day["success"] for day in result["trend"]) >= 1
    assert all(not item.get("response") for item in result["recent"])


@pytest.mark.asyncio
async def test_notification_outage_is_isolated_and_persistently_rate_limited(monkeypatch, tmp_path):
    from backend.app import maintenance
    from backend.app.security import encrypt

    ledger = RelayLedger(tmp_path)
    ledger.reserve("@target", "outage", "text", "-100", 1)
    ledger.finish("@target", "outage", 1, False, "FloodWait")
    monkeypatch.setattr(maintenance, "get_ledger", lambda: ledger)
    set_setting("notifications", encrypt('[{"type":"webhook","enabled":true}]'))
    set_setting("maintenance_policy", "{}")
    calls = []

    async def broken_provider(*args):
        calls.append(True)
        raise RuntimeError("sensitive-url-must-not-escape")

    monkeypatch.setattr(maintenance, "notify", broken_provider)
    await maintenance.check_relay_alerts()
    assert ledger.alert_status()["last_status"] == "failed"
    monkeypatch.setattr(maintenance, "get_ledger", lambda: RelayLedger(tmp_path))
    await maintenance.check_relay_alerts()
    assert len(calls) == 1
    set_setting("notifications", "")


def test_overview_respects_local_day_boundaries(admin, account):
    set_setting("timezone", "Asia/Shanghai")
    local = timezone(timedelta(hours=8))
    today = datetime.now(local).replace(hour=0, minute=0, second=0, microsecond=0)
    with Session() as db:
        db.query(Run).delete()
        for stamp, status in [(today - timedelta(seconds=1), "failed"), (today, "success")]:
            db.add(
                Run(
                    task_name="boundary",
                    account_name="test",
                    account_id=account,
                    started_at=stamp.astimezone(timezone.utc).isoformat(),
                    status=status,
                )
            )
        db.commit()
    result = admin.get("/api/overview").json()
    assert result["today_runs"] == 1
    assert result["trend"][-1]["success"] == 1
    assert result["trend"][-2]["failed"] == 1
