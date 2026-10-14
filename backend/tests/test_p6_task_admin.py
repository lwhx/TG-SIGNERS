"""P6 grouping, cloning, revisions, restore guards and authenticated batch API."""
import sqlite3
from pathlib import Path

from backend.app import db as app_db
from backend.app.db import Session, Task
from backend.tests.test_api import task_payload


def test_task_versions_duplicate_and_restore(admin, account):
    payload = task_payload(account, name="P6 initial", group_name="115 转存")
    created = admin.post("/api/tasks", json=payload)
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    assert created.json()["group_name"] == "115 转存"
    rev = admin.get("/api/tasks/"+str(task_id)+"/versions")
    assert rev.status_code == 200, rev.text
    assert rev.json()["current_version"] == 1
    assert rev.json()["items"][0]["operation"] == "created"

    copied = admin.post("/api/tasks/"+str(task_id)+"/duplicate", json={})
    assert copied.status_code == 201, copied.text
    copy_id = copied.json()["id"]
    clone = admin.get("/api/tasks/"+str(copy_id)).json()
    assert clone["enabled"] is False
    assert clone["group_name"] == "115 转存"
    assert clone["folder"] == created.json()["folder"]
    assert clone["config"] == created.json()["config"]
    assert clone["name"].startswith("副本")
    assert admin.get("/api/tasks/"+str(copy_id)+"/versions").json()["current_version"] == 1

    updated = admin.put("/api/tasks/"+str(task_id), json={**payload,
        "name": "P6 edited", "group_name": "日常签到"})
    assert updated.status_code == 200, updated.text
    list_response = admin.get("/api/tasks/"+str(task_id)+"/versions").json()
    assert list_response["current_version"] == 2
    old = admin.get("/api/tasks/"+str(task_id)+"/versions/1").json()
    assert old["snapshot"]["name"] == "P6 initial"
    assert old["snapshot"]["group_name"] == "115 转存"
    assert "session" not in str(old)
    stale = admin.post("/api/tasks/"+str(task_id)+"/versions/1/restore",
        json={"expected_version": 1})
    assert stale.status_code == 409
    restored = admin.post("/api/tasks/"+str(task_id)+"/versions/1/restore",
        json={"expected_version": 2})
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3
    result = admin.get("/api/tasks/"+str(task_id)).json()
    assert result["name"] == "P6 initial"
    assert result["group_name"] == "115 转存"
    assert result["enabled"] is False
    assert admin.get("/api/tasks/"+str(task_id)+"/versions").json()["items"][0]["operation"] == "restored:1"
    admin.delete("/api/tasks/"+str(copy_id))
    admin.delete("/api/tasks/"+str(task_id))


def test_batch_preflight_group_move_confirmation_and_atomic_validation(admin, account):
    first = admin.post("/api/tasks", json=task_payload(account, name="bulk-1")).json()["id"]
    second = admin.post("/api/tasks", json=task_payload(account, name="bulk-2")).json()["id"]
    ids = [first, second]
    preflight = admin.post("/api/tasks/batch", json={
        "ids": ids, "operation": "move_group", "group_name": "媒体组", "dry_run": True,
    })
    assert preflight.status_code == 200
    assert preflight.json()["count"] == 2
    assert not admin.get("/api/tasks/"+str(first)).json()["group_name"]

    changed = admin.post("/api/tasks/batch", json={
        "ids": ids, "operation": "move_group", "group_name": "媒体组",
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["count"] == 2
    assert admin.get("/api/tasks/"+str(second)).json()["group_name"] == "媒体组"
    assert admin.get("/api/tasks/"+str(first)+"/versions").json()["current_version"] == 2
    assert admin.post("/api/tasks/batch", json={
        "ids": [first, 9999999], "operation": "disable",
    }).status_code == 404
    assert admin.post("/api/tasks/batch", json={
        "ids": [first, first], "operation": "disable",
    }).status_code == 422
    assert admin.post("/api/tasks/batch", json={
        "ids": ids, "operation": "delete",
    }).status_code == 400
    assert admin.get("/api/tasks/"+str(first)).status_code == 200
    deleted = admin.post("/api/tasks/batch", json={
        "ids": ids, "operation": "delete", "confirmation": "DELETE",
    })
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["count"] == 2
    assert admin.get("/api/tasks/"+str(first)).status_code == 404


def test_group_name_rejects_control_characters(admin, account):
    payload = task_payload(account, group_name="bad\nfolder")
    assert admin.post("/api/tasks", json=payload).status_code == 422
    assert admin.post("/api/tasks", json={**payload, "group_name": " 作品 "}).json()["group_name"] == "作品"


def test_group_migration_preserves_telegram_folder(monkeypatch, tmp_path):
    """Simulate a P5 tasks table without group_name, then migrate in place."""
    from sqlalchemy import create_engine

    path = Path(tmp_path) / "old.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, folder TEXT)")
    con.execute("INSERT INTO tasks(id,folder) VALUES(1,'TelegramFolder')")
    con.commit()
    con.close()
    temporary = create_engine("sqlite:///"+str(path))
    monkeypatch.setattr(app_db, "engine", temporary)
    app_db.migrate_task_groups()
    app_db.migrate_task_groups()
    with sqlite3.connect(path) as con:
        row = con.execute("SELECT folder,group_name FROM tasks WHERE id=1").fetchone()
    assert row == ("TelegramFolder", "")
    temporary.dispose()
