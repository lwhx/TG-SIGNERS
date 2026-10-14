"""Persistent and backward-compatible delivery ledger for Telegram relays.

No message text or Telegram credentials are stored in this SQLite database.
Legacy P1 rows remain readable but cannot be manually replayed without
provenance. Every mutation is guarded by an atomic SQLite transaction.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


class RelayLedger:
    """Idempotency reservations, delivery history, stats and safe replay leases."""

    _EXTRA_COLUMNS = {
        "account_id": "INTEGER",
        "task_id": "INTEGER",
        "rule_id": "TEXT",
        "handler_index": "INTEGER",
        "target_index": "INTEGER",
        "part_index": "INTEGER",
        "dedup": "TEXT",
        "payload_digest": "TEXT",
    }
    _STATUSES = frozenset({"sent", "failed", "sending"})

    def __init__(self, workdir: Path) -> None:
        self.path = Path(workdir) / "message_relay.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """CREATE TABLE IF NOT EXISTS relay_deliveries (
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
            current = {
                row["name"] for row in db.execute("PRAGMA table_info(relay_deliveries)")
            }
            for name, sql_type in self._EXTRA_COLUMNS.items():
                if name not in current:
                    db.execute(
                        f"ALTER TABLE relay_deliveries ADD COLUMN {name} {sql_type}"
                    )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_status_updated "
                "ON relay_deliveries (status, updated_at DESC)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_task_updated "
                "ON relay_deliveries (task_id, updated_at DESC)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_account_updated "
                "ON relay_deliveries (account_id, updated_at DESC)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            with db:
                yield db

    @staticmethod
    def hash_value(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def _as_item(cls, row: sqlite3.Row) -> dict:
        entry = dict(row)
        entry["retryable"] = (
            entry["status"] == "failed"
            and entry["source_message"] is not None
            and all(
                entry.get(field) is not None
                for field in (
                    "account_id",
                    "task_id",
                    "rule_id",
                    "handler_index",
                    "target_index",
                    "part_index",
                    "dedup",
                    "payload_digest",
                )
            )
        )
        return entry

    def reserve(
        self,
        destination: str,
        fingerprint: str,
        action: str,
        source_chat: str,
        source_message: int | None,
        retry_after_seconds: int = 600,
        lease_seconds: int = 300,
        *,
        force_failed: bool = False,
        account_id: int | None = None,
        task_id: int | None = None,
        rule_id: str | None = None,
        handler_index: int | None = None,
        target_index: int | None = None,
        part_index: int | None = None,
        dedup: str | None = None,
        payload_digest: str | None = None,
    ) -> tuple[str, int | None]:
        """Return (reserved, attempt) or (skip_reason, None).

        force_failed bypasses cooldown ONLY for an existing failed record; it
        never creates a new delivery or overrides sent/in-flight records.
        """
        stamp = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, attempts, updated_at FROM relay_deliveries "
                "WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone()
            if row:
                state, attempts, updated = (
                    row["status"],
                    row["attempts"],
                    row["updated_at"],
                )
                if state == "sent":
                    return "duplicate", None
                if state == "sending" and stamp - updated < lease_seconds:
                    return "inflight", None
                if state == "failed" and not force_failed and (
                    stamp - updated < retry_after_seconds
                ):
                    return "cooldown", None
                if force_failed and state != "failed":
                    return "not_failed", None
                next_attempt = attempts + 1
                db.execute(
                    "UPDATE relay_deliveries SET status='sending', attempts=?, "
                    "updated_at=?, last_error=NULL WHERE destination=? "
                    "AND fingerprint=?",
                    (next_attempt, stamp, destination, fingerprint),
                )
            else:
                if force_failed:
                    return "not_found", None
                next_attempt = 1
                db.execute(
                    "INSERT INTO relay_deliveries "
                    "(destination,fingerprint,action,source_chat,source_message,"
                    "status,attempts,created_at,updated_at,account_id,task_id,"
                    "rule_id,handler_index,target_index,part_index,dedup,payload_digest) "
                    "VALUES (?,?,?,?,?,'sending',?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        destination, fingerprint, action, source_chat,
                        source_message, next_attempt, stamp, stamp, account_id,
                        task_id, rule_id, handler_index, target_index, part_index,
                        dedup, payload_digest,
                    ),
                )
        return "reserved", next_attempt

    def finish(
        self,
        destination: str,
        fingerprint: str,
        attempt: int,
        succeeded: bool,
        error_type: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE relay_deliveries SET status=?, updated_at=?, "
                "last_error=? WHERE destination=? AND fingerprint=? "
                "AND status='sending' AND attempts=?",
                (
                    "sent" if succeeded else "failed", int(time.time()),
                    error_type, destination, fingerprint, attempt,
                ),
            )

    def get(self, destination: str, fingerprint: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM relay_deliveries "
                "WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone()
        return self._as_item(row) if row else None

    def history(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
        account_id: int | None = None,
        task_id: int | None = None,
    ) -> dict:
        if not 1 <= page <= 100000 or not 1 <= page_size <= 100:
            raise ValueError("invalid pagination")
        if status is not None and status not in self._STATUSES:
            raise ValueError("invalid delivery status")
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if account_id is not None:
            where.append("account_id = ?")
            params.append(account_id)
        if task_id is not None:
            where.append("task_id = ?")
            params.append(task_id)
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM relay_deliveries" + sql_where, params
            ).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM relay_deliveries" + sql_where
                + " ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [self._as_item(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def stats(self, *, account_id: int | None = None,
              task_id: int | None = None) -> dict:
        where, params = [], []
        if account_id is not None:
            where.append("account_id = ?")
            params.append(account_id)
        if task_id is not None:
            where.append("task_id = ?")
            params.append(task_id)
        suffix = (" WHERE " + " AND ".join(where)) if where else ""
        cutoff = int(time.time()) - 86400
        with self._connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS total FROM relay_deliveries"
                + suffix + " GROUP BY status", params,
            ).fetchall()
            recent_where = (" WHERE " + " AND ".join(
                [*where, "updated_at >= ?"]
            ))
            last_day = db.execute(
                "SELECT COUNT(*) FROM relay_deliveries" + recent_where,
                [*params, cutoff],
            ).fetchone()[0]
            week_cutoff = int(time.time()) - 7 * 86400
            days = db.execute(
                "SELECT date(updated_at, 'unixepoch') AS day, status, "
                "COUNT(*) AS total FROM relay_deliveries WHERE "
                + " AND ".join([*where, "updated_at >= ?"])
                + " GROUP BY day,status",
                [*params, week_cutoff],
            ).fetchall()
        counts = {"sent": 0, "failed": 0, "sending": 0}
        counts.update({row["status"]: row["total"] for row in rows})
        series = {}
        for row in days:
            series.setdefault(
                row["day"], {"sent": 0, "failed": 0, "sending": 0}
            )[row["status"]] = row["total"]
        today = datetime.now(timezone.utc).date()
        chart = [
            {"date": date.isoformat(), **series.get(
                date.isoformat(), {"sent": 0, "failed": 0, "sending": 0}
            )}
            for date in (today - timedelta(days=offset) for offset in range(6, -1, -1))
        ]
        return {
            "total": sum(counts.values()),
            "last_24h": last_day,
            **counts,
            "success_rate": (
                round(counts["sent"] / (counts["sent"] + counts["failed"]) * 100, 1)
                if counts["sent"] + counts["failed"] else None
            ),
            "days": chart,
        }
