"""P8 backup crypto, WAL snapshots, tampering, cold recovery and API security."""

import json
import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from backend.app import backup_api, performance
from backend.app.backup_core import (
    BackupError,
    _atomic_bytes,
    _derive,
    apply_pending_restore,
    backup_path,
    create_backup,
    import_backup,
    list_backups,
    queue_restore,
    restore_status,
    verify_backup,
)
from backend.app.schema_migrations import CURRENT_SCHEMA, migrate_schema, schema_status


PASSWORD = "strong-backup-secret-2026"


def sandbox_data(root: Path) -> Path:
    data = root / "instance"
    (data / "upstream").mkdir(parents=True)
    (data / "encryption.key").write_text(Fernet.generate_key().decode())
    (data / "jwt.key").write_text("jwt-should-be-private")
    (data / "upstream" / "handlers").mkdir()
    (data / "upstream" / "handlers" / "custom.py").write_text("# restored handler")
    with sqlite3.connect(data / "dashboard.sqlite3") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY,name TEXT)")
        db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE settings (key TEXT)")
        db.execute("CREATE TABLE runs (id INTEGER)")
        db.execute("INSERT INTO accounts VALUES (1,'original')")
    with sqlite3.connect(data / "upstream" / "message_relay.sqlite3") as db:
        db.execute("CREATE TABLE relay_deliveries (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO relay_deliveries VALUES (99)")
    return data


def read_name(data: Path) -> str:
    with sqlite3.connect(data / "dashboard.sqlite3") as db:
        return db.execute("SELECT name FROM accounts WHERE id=1").fetchone()[0]


def update_name(data: Path, name: str):
    with sqlite3.connect(data / "dashboard.sqlite3") as db:
        db.execute("UPDATE accounts SET name=? WHERE id=1", (name,))


def test_encrypted_backup_is_consistent_and_not_plaintext(tmp_path, monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    data = sandbox_data(tmp_path)
    created = create_backup(data, PASSWORD)
    assert created["files"] >= 5
    path = backup_path(data, created["id"])
    raw = path.read_bytes()
    assert b"original" not in raw
    assert b"jwt-should-be-private" not in raw
    assert len(list_backups(data)) == 1
    assert verify_backup(data, created["id"], PASSWORD)["valid"] is True
    with pytest.raises(BackupError, match="密码错误|损坏"):
        verify_backup(data, created["id"], "incorrect-password-123")
    tampered = bytearray(raw)
    tampered[-20] ^= 1
    path.write_bytes(tampered)
    with pytest.raises(BackupError, match="密码错误|损坏"):
        verify_backup(data, created["id"], PASSWORD)


def test_import_verify_stage_and_cold_restore_with_rollback_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    data = sandbox_data(tmp_path)
    created = create_backup(data, PASSWORD)
    blob = backup_path(data, created["id"]).read_bytes()
    imported = import_backup(data, blob, PASSWORD)
    assert imported["id"] != created["id"]
    assert verify_backup(data, imported["id"], PASSWORD)["valid"]
    with pytest.raises(BackupError):
        import_backup(data, blob, "wrong-passphrase-2026")
    update_name(data, "latest")
    (data / "jwt.key").write_text("changed-key")
    queued = queue_restore(data, imported["id"], PASSWORD)
    assert queued["pending"] is True
    assert read_name(data) == "latest", "queue must never hot-replace live data"
    assert (data / "jwt.key").read_text() == "changed-key"
    with pytest.raises(BackupError, match="已有等待执行"):
        queue_restore(data, imported["id"], PASSWORD)
    result = apply_pending_restore(data)
    assert result["status"] == "completed"
    assert read_name(data) == "original"
    assert (data / "jwt.key").read_text() == "jwt-should-be-private"
    assert (data / ".restore-previous" / imported["id"] / "jwt.key").read_text() == "changed-key"
    assert restore_status(data)["last"]["status"] == "completed"
    assert restore_status(data)["pending"] is None
    assert apply_pending_restore(data) is None


def test_cold_restore_recovers_interrupted_multi_file_swap(tmp_path):
    data = sandbox_data(tmp_path)
    old_key = (data / "jwt.key").read_bytes()
    snapshot = (data / "dashboard.sqlite3").read_bytes()
    backup_id = "f" * 32
    previous = data / ".restore-previous" / backup_id
    previous.mkdir(parents=True)
    (previous / "jwt.key").write_bytes(old_key)
    (previous / "dashboard.sqlite3").write_bytes(snapshot)
    (data / "jwt.key").write_text("partial-key")
    (data / "dashboard.sqlite3").write_text("broken database")
    journal = {
        "id": backup_id,
        "state": "applying",
        "replace": ["jwt.key", "dashboard.sqlite3", "new-key.txt"],
        "existing": ["jwt.key", "dashboard.sqlite3"],
    }
    _atomic_bytes(data / ".restore-journal.json", json.dumps(journal).encode())
    (data / ".pending_restore.json").write_text(json.dumps({"id": backup_id}))
    (data / "new-key.txt").write_text("partial")
    result = apply_pending_restore(data)
    assert result["status"] == "rolled_back"
    assert read_name(data) == "original"
    assert (data / "jwt.key").read_bytes() == old_key
    assert not (data / "new-key.txt").exists()
    assert restore_status(data)["pending"] is None
    assert restore_status(data)["last"]["status"] == "rolled_back"


def test_symlink_and_missing_key_and_bad_snapshot_fail_closed(tmp_path):
    data = sandbox_data(tmp_path)
    (data / "upstream" / "handlers" / "external.py").symlink_to(data / "jwt.key")
    with pytest.raises(BackupError, match="符号链接"):
        create_backup(data, PASSWORD)
    (data / "upstream" / "handlers" / "external.py").unlink()
    (data / "jwt.key").unlink()
    with pytest.raises(BackupError, match="密钥"):
        create_backup(data, PASSWORD)
    (data / "jwt.key").write_text("valid")
    (data / "upstream" / "message_relay.sqlite3").write_text("corrupted")
    with pytest.raises((BackupError, sqlite3.DatabaseError)):
        create_backup(data, PASSWORD)


def test_schema_history_is_idempotent_and_compatible_with_existing_api(admin):
    first = migrate_schema()
    again = migrate_schema()
    assert first["current"] == CURRENT_SCHEMA
    assert again["migrations"] == first["migrations"]
    assert len(first["migrations"]) == CURRENT_SCHEMA
    result = admin.get("/api/maintenance/performance/schema")
    assert result.status_code == 200
    assert result.json()["current"] == CURRENT_SCHEMA
    assert schema_status()["supported"] == CURRENT_SCHEMA


def test_backup_api_auth_and_management(admin, client, tmp_path, monkeypatch):
    data = sandbox_data(tmp_path)
    monkeypatch.setattr(backup_api, "DATA", data)
    bad_headers = {"Cookie": "tg_dashboard=not-valid"}
    assert client.get("/api/maintenance/backups", headers=bad_headers).status_code == 401
    assert client.post("/api/maintenance/backups", json={
        "passphrase": PASSWORD,
    }, headers=bad_headers).status_code == 401
    created = admin.post("/api/maintenance/backups", json={"passphrase":PASSWORD})
    assert created.status_code == 201, created.text
    backup_id = created.json()["id"]
    download = admin.get("/api/maintenance/backups/"+backup_id+"/download")
    assert download.status_code == 200
    assert b"original" not in download.content
    verified = admin.post("/api/maintenance/backups/"+backup_id+"/verify",json={
        "passphrase":PASSWORD,
    })
    assert verified.status_code == 200, verified.text
    form = {"passphrase":PASSWORD}
    uploaded = admin.post("/api/maintenance/backups/import",data=form,files={
        "file":("other-machine.tgsb",download.content,"application/octet-stream"),
    })
    assert uploaded.status_code == 201, uploaded.text
    imported_id = uploaded.json()["id"]
    assert admin.post("/api/maintenance/backups/"+backup_id+"/restore",json={
        "passphrase":PASSWORD,"confirmation":"NO",
    }).status_code == 400
    scheduled = admin.post("/api/maintenance/backups/"+imported_id+"/restore",json={
        "passphrase":PASSWORD,"confirmation":"RESTORE",
    })
    assert scheduled.status_code == 200, scheduled.text
    assert admin.get("/api/maintenance/backups/restore-status").json()["pending"]["id"] == imported_id
    assert admin.request("DELETE","/api/maintenance/backups/"+imported_id,json={
        "confirmation":"DELETE",
    }).status_code == 400
    assert admin.request("DELETE","/api/maintenance/backups/"+backup_id,json={
        "confirmation":"DELETE",
    }).status_code == 200


def test_performance_snapshots_are_bounded(admin, monkeypatch, tmp_path):
    path = tmp_path / "performance-samples.json"
    monkeypatch.setattr(performance, "HISTORY_FILE", path)
    status = admin.get("/api/maintenance/performance")
    assert status.status_code == 200, status.text
    first = admin.post("/api/maintenance/performance/measure")
    assert first.status_code == 200, first.text
    assert first.json()["probe_ms"] >= 0
    assert first.json()["dashboard"]["p50_ms"] is not None
    assert len(json.loads(path.read_text())) == 1
    assert admin.get("/api/maintenance/performance").json()["history"]
