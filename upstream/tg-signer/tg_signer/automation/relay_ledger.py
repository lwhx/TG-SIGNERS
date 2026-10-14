"""Persistent idempotency and delivery history for generic message relays."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator


class RelayLedger:
    """Reserve delivery attempts atomically across tasks and processes."""

    def __init__(self, workdir: Path) -> None:
        self.path = Path(workdir) / "message_relay.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        with self._connect() as db:
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

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Connection context managers commit but do not close; closing does both.
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            with db:
                yield db

    @staticmethod
    def hash_value(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def reserve(
        self,
        destination: str,
        fingerprint: str,
        action: str,
        source_chat: str,
        source_message: int | None,
        retry_after_seconds: int = 600,
        lease_seconds: int = 300,
    ) -> tuple[str, int | None]:
        """Return (reserved, attempt) or a skip reason and None."""
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, attempts, updated_at FROM relay_deliveries "
                "WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone()
            if row:
                state, attempts, updated = row
                if state == "sent":
                    return "duplicate", None
                if state == "sending" and now - updated < lease_seconds:
                    return "inflight", None
                if state == "failed" and now - updated < retry_after_seconds:
                    return "cooldown", None
                next_attempt = attempts + 1
                db.execute(
                    "UPDATE relay_deliveries SET status='sending', attempts=?, "
                    "updated_at=?, last_error=NULL, action=?, source_chat=?, "
                    "source_message=? WHERE destination=? AND fingerprint=?",
                    (
                        next_attempt, now, action, source_chat, source_message,
                        destination, fingerprint,
                    ),
                )
            else:
                next_attempt = 1
                db.execute(
                    "INSERT INTO relay_deliveries "
                    "(destination,fingerprint,action,source_chat,source_message,"
                    "status,attempts,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,'sending',?,?,?)",
                    (
                        destination, fingerprint, action, source_chat,
                        source_message, next_attempt, now, now,
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
