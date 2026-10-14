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

from .bot_results import BotResultMixin
from .relay_recovery import RelayRecoveryMixin


class RelayLedger(RelayRecoveryMixin, BotResultMixin):
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
        "failure_seq": "INTEGER NOT NULL DEFAULT 0",
        "failure_kind": "TEXT",
        "lease_until": "INTEGER",
        "next_retry_at": "INTEGER",
        "retry_floor": "INTEGER NOT NULL DEFAULT 600",
        "auto_attempts": "INTEGER NOT NULL DEFAULT 0",
        "retry_paused": "INTEGER NOT NULL DEFAULT 0",
        "retry_claim": "TEXT",
        "retry_claim_until": "INTEGER",
        "retry_status": "TEXT NOT NULL DEFAULT 'none'",
        "last_retry_error": "TEXT",
        "config_digest": "TEXT",
        "source_digest": "TEXT",
        "resolution": "TEXT",
        "resolved_at": "INTEGER",
        "destination_message": "INTEGER",
        "media_count": "INTEGER NOT NULL DEFAULT 1",
        "media_group_key": "TEXT",
    }
    _STATUSES = frozenset({"sent", "failed", "sending", "uncertain"})

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
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_updated "
                "ON relay_deliveries (updated_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_failure_seq "
                "ON relay_deliveries (failure_seq) WHERE status='failed'"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS relay_dedup ("
                "destination TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                "PRIMARY KEY(destination, fingerprint)) WITHOUT ROWID"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS relay_suppressed ("
                "destination TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                "PRIMARY KEY(destination, fingerprint)) WITHOUT ROWID"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS relay_alert_state ("
                "id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL DEFAULT 0, "
                "ack_sequence INTEGER NOT NULL DEFAULT 0, next_allowed INTEGER NOT NULL DEFAULT 0, "
                "first_since INTEGER, "
                "last_status TEXT NOT NULL DEFAULT 'idle')"
            )
            db.execute("INSERT OR IGNORE INTO relay_alert_state(id) VALUES(1)")
            self._init_bot_results(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS relay_reviews (destination TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                "attempt INTEGER NOT NULL, decision TEXT NOT NULL, resolved_at INTEGER NOT NULL, "
                "PRIMARY KEY(destination,fingerprint,attempt))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_outcome_seq ON relay_deliveries(failure_seq)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_retry_due ON relay_deliveries(next_retry_at) WHERE status='failed'"
            )
        # Read-only history requests must not request a write lock for WAL setup.
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")

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
        entry.pop("retry_claim", None)
        entry["needs_review"] = entry["status"] == "uncertain" or (
            entry["status"] == "failed" and entry.get("failure_kind") is None
        )
        entry["retryable"] = (
            entry["status"] == "failed"
            and not entry["needs_review"]
            and entry["action"] not in {"album_forward", "album_copy"}
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
        config_digest: str | None = None,
        source_digest: str | None = None,
        retry_claim: str | None = None,
        media_count: int = 1,
        media_group_key: str | None = None,
    ) -> tuple[str, int | None]:
        """Return (reserved, attempt) or (skip_reason, None).

        force_failed bypasses cooldown ONLY for an existing failed record; it
        never creates a new delivery or overrides sent/in-flight records.
        """
        stamp = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM relay_dedup WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone():
                return "duplicate", None
            if db.execute(
                "SELECT 1 FROM relay_suppressed WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone():
                return "history_removed", None
            row = db.execute(
                "SELECT * FROM relay_deliveries WHERE destination=? AND fingerprint=?",
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
                if state == "uncertain":
                    return "uncertain", None
                if state == "sending":
                    if stamp < (
                        row["lease_until"] or updated + max(120, lease_seconds)
                    ):
                        return "inflight", None
                    self._mark_uncertain(db, destination, fingerprint, "LeaseExpired")
                    return "uncertain", None
                if row["failure_kind"] is None or (
                    row["failure_kind"] == "reviewed" and not force_failed
                ):
                    return "review_required", None
                if row["failure_kind"] == "permanent" and not force_failed:
                    return "permanent_failure", None
                if not force_failed:
                    return "failed_requires_retry", None
                if retry_claim:
                    if (
                        row["retry_claim"] != retry_claim
                        or (row["retry_claim_until"] or 0) <= stamp
                    ):
                        return "claim_expired", None
                elif row["retry_claim"] and (row["retry_claim_until"] or 0) > stamp:
                    return "retry_claimed", None
                next_attempt = attempts + 1
                db.execute(
                    "UPDATE relay_deliveries SET status='sending', attempts=?, "
                    "updated_at=?, last_error=NULL,lease_until=?,next_retry_at=NULL, "
                    "retry_floor=?,retry_status='sending',resolution=NULL,resolved_at=NULL WHERE destination=? "
                    "AND fingerprint=?",
                    (
                        next_attempt,
                        stamp,
                        stamp + max(120, lease_seconds),
                        retry_after_seconds,
                        destination,
                        fingerprint,
                    ),
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
                        destination,
                        fingerprint,
                        action,
                        source_chat,
                        source_message,
                        next_attempt,
                        stamp,
                        stamp,
                        account_id,
                        task_id,
                        rule_id,
                        handler_index,
                        target_index,
                        part_index,
                        dedup,
                        payload_digest,
                    ),
                )
                db.execute(
                    "UPDATE relay_deliveries SET lease_until=?,retry_floor=?,config_digest=?,source_digest=?,"
                    "media_count=?,media_group_key=? WHERE destination=? AND fingerprint=?",
                    (
                        stamp + max(120, lease_seconds),
                        retry_after_seconds,
                        config_digest,
                        source_digest,
                        max(1, min(10, media_count)),
                        media_group_key,
                        destination,
                        fingerprint,
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
        *,
        failure_kind: str = "permanent",
        retry_after_seconds: int = 0,
        destination_message: int | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT retry_floor,action FROM relay_deliveries WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone()
            stamp = int(time.time())
            state = (
                "sent"
                if succeeded
                else "uncertain"
                if failure_kind == "unknown"
                else "failed"
            )
            due = (
                stamp + max(1, retry_after_seconds, row["retry_floor"])
                if row
                and not succeeded
                and failure_kind == "transient"
                and row["action"] in {"text", "extract", "template"}
                else None
            )
            result = db.execute(
                "UPDATE relay_deliveries SET status=?, updated_at=?, "
                "last_error=?,failure_kind=?,next_retry_at=?,retry_status=?,destination_message=?,retry_floor=MAX(retry_floor,?), "
                "resolution=CASE WHEN ? AND resolution='not_sent' THEN 'telegram_sent' ELSE resolution END "
                "WHERE destination=? AND fingerprint=? "
                "AND (status='sending' OR (status='uncertain' AND resolution IS NULL) "
                "OR (? AND status='failed' AND resolution='not_sent')) AND attempts=?",
                (
                    state,
                    stamp,
                    error_type,
                    None if succeeded else failure_kind,
                    due,
                    "scheduled"
                    if due
                    else "none"
                    if succeeded
                    else "manual_only"
                    if failure_kind == "transient"
                    and row
                    and row["action"] in {"forward", "copy", "album_forward", "album_copy"}
                    else "blocked",
                    destination_message,
                    retry_after_seconds,
                    succeeded,
                    destination,
                    fingerprint,
                    succeeded,
                    attempt,
                ),
            )
            if result.rowcount and not succeeded:
                db.execute(
                    "UPDATE relay_alert_state SET sequence=sequence+1 WHERE id=1"
                )
                db.execute(
                    "UPDATE relay_deliveries SET failure_seq="
                    "(SELECT sequence FROM relay_alert_state WHERE id=1) "
                    "WHERE destination=? AND fingerprint=?",
                    (destination, fingerprint),
                )

    def cleanup(
        self,
        *,
        sent_before: int,
        failed_before: int,
        limit: int = 500,
        dry_run: bool = False,
    ) -> int:
        """Remove bounded terminal history, retaining successful dedup keys forever.

        An in-flight delivery is never eligible, including an expired lease.
        Selection, tombstones and deletion share the writer transaction.
        """
        if not 1 <= limit <= 5000:
            raise ValueError("invalid cleanup batch size")
        predicate = (
            "(status='sent' AND updated_at < ?) OR "
            "(status='failed' AND failure_kind IS NOT NULL AND updated_at < ?)"
        )
        predicate = (
            "("
            + predicate
            + ") AND next_retry_at IS NULL AND COALESCE(retry_claim_until,0)=0"
        )
        with self._connect() as db:
            if dry_run:
                return db.execute(
                    "SELECT COUNT(*) FROM relay_deliveries WHERE " + predicate,
                    (sent_before, failed_before),
                ).fetchone()[0]
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT rowid, destination, fingerprint, status, dedup FROM relay_deliveries "
                "WHERE " + predicate + " ORDER BY updated_at LIMIT ?",
                (sent_before, failed_before, limit),
            ).fetchall()
            db.executemany(
                "INSERT OR IGNORE INTO relay_dedup(destination, fingerprint) VALUES(?,?)",
                [
                    (r["destination"], r["fingerprint"])
                    for r in rows
                    if r["status"] == "sent" and r["dedup"] != "off"
                ],
            )
            db.executemany(
                "INSERT OR IGNORE INTO relay_suppressed(destination, fingerprint) VALUES(?,?)",
                [
                    (r["destination"], r["fingerprint"])
                    for r in rows
                    if r["status"] == "failed"
                ],
            )
            db.executemany(
                "DELETE FROM relay_bot_results WHERE destination=? AND fingerprint=?",
                [(r["destination"], r["fingerprint"]) for r in rows],
            )
            db.executemany(
                "DELETE FROM relay_deliveries WHERE rowid=?",
                [(r["rowid"],) for r in rows],
            )
            return len(rows)

    def claim_alert(
        self, *, since: int, cooldown: int, stamp: int | None = None
    ) -> dict | None:
        """Persistently lease one summary of new failures; no payloads or destinations."""
        stamp = int(time.time()) if stamp is None else stamp
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT * FROM relay_alert_state WHERE id=1").fetchone()
            if stamp < state["next_allowed"]:
                return None
            # Initial activation has a bounded lookback. Thereafter the durable
            # cursor includes failures accumulated during cooldown or downtime.
            since = state["first_since"] if state["first_since"] is not None else since
            summary = db.execute(
                "SELECT COUNT(*) AS total, MAX(failure_seq) AS seq FROM relay_deliveries "
                "WHERE status IN ('failed','uncertain') AND updated_at>=? AND failure_seq>?",
                (since, state["ack_sequence"]),
            ).fetchone()
            if not summary["total"]:
                return None
            groups = db.execute(
                "SELECT account_id,task_id,last_error,COUNT(*) AS total,MAX(failure_seq) AS seq "
                "FROM relay_deliveries WHERE status IN ('failed','uncertain') AND updated_at>=? AND failure_seq>? "
                "GROUP BY account_id,task_id,last_error ORDER BY seq DESC LIMIT 10",
                (since, state["ack_sequence"]),
            ).fetchall()
            db.execute(
                "UPDATE relay_alert_state SET next_allowed=?, first_since=COALESCE(first_since,?), "
                "last_status='sending' WHERE id=1",
                (stamp + cooldown, since),
            )
        return {
            "sequence": summary["seq"],
            "total": summary["total"],
            "groups": [dict(r) for r in groups],
        }

    def complete_alert(self, sequence: int, success: bool) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE relay_alert_state SET ack_sequence=MAX(ack_sequence,?), "
                "last_status=? WHERE id=1",
                (sequence if success else 0, "sent" if success else "failed"),
            )

    def alert_status(self) -> dict:
        with self._connect() as db:
            return dict(
                db.execute(
                    "SELECT next_allowed,last_status FROM relay_alert_state WHERE id=1"
                ).fetchone()
            )

    def get(self, destination: str, fingerprint: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM relay_deliveries WHERE destination=? AND fingerprint=?",
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
                "SELECT * FROM relay_deliveries"
                + sql_where
                + " ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [self._as_item(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def stats(
        self, *, account_id: int | None = None, task_id: int | None = None
    ) -> dict:
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
                + suffix
                + " GROUP BY status",
                params,
            ).fetchall()
            recent_where = " WHERE " + " AND ".join([*where, "updated_at >= ?"])
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
        counts = {"sent": 0, "failed": 0, "sending": 0, "uncertain": 0}
        counts.update({row["status"]: row["total"] for row in rows})
        series = {}
        for row in days:
            series.setdefault(
                row["day"], {"sent": 0, "failed": 0, "sending": 0, "uncertain": 0}
            )[row["status"]] = row["total"]
        today = datetime.now(timezone.utc).date()
        chart = [
            {
                "date": date.isoformat(),
                **series.get(
                    date.isoformat(),
                    {"sent": 0, "failed": 0, "sending": 0, "uncertain": 0},
                ),
            }
            for date in (today - timedelta(days=offset) for offset in range(6, -1, -1))
        ]
        return {
            "total": sum(counts.values()),
            "last_24h": last_day,
            **counts,
            "success_rate": (
                round(counts["sent"] / (counts["sent"] + counts["failed"]) * 100, 1)
                if counts["sent"] + counts["failed"]
                else None
            ),
            "days": chart,
        }
