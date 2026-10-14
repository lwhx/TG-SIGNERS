"""P7 opt-in 115 Bot result tracking, separate from Telegram delivery outcomes.

A Telegram send is never proof of transfer/storage by the target Bot.
Only explicit replies to the exact sent message from the Bot's own chat
may change the result. We store metadata and text hashes, never reply text.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time

import regex


class BotResultMixin:
    @staticmethod
    def _init_bot_results(db: sqlite3.Connection) -> None:
        db.execute(
            """CREATE TABLE IF NOT EXISTS relay_bot_results (
                destination TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                account_id INTEGER NOT NULL,
                task_id INTEGER,
                bot_chat INTEGER NOT NULL,
                sent_message INTEGER NOT NULL,
                success_pattern TEXT NOT NULL,
                failure_pattern TEXT NOT NULL,
                deadline INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reply_message INTEGER,
                reply_digest TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(destination,fingerprint),
                UNIQUE(account_id,bot_chat,sent_message)
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_results_account_time "
            "ON relay_bot_results(account_id,updated_at DESC)"
        )

    def track_bot_send(
        self,
        *,
        destination: str,
        fingerprint: str,
        account_id: int,
        task_id: int | None,
        bot_chat: int,
        sent_message: int,
        success_pattern: str,
        failure_pattern: str,
        timeout_minutes: int,
    ) -> bool:
        """Register only already-confirmed Telegram deliveries; duplicate-safe."""
        if (
            type(timeout_minutes) is not int
            or not 1 <= timeout_minutes <= 10080
            or not isinstance(success_pattern, str)
            or not success_pattern
            or not isinstance(failure_pattern, str)
            or not failure_pattern
            or len(success_pattern) > 500
            or len(failure_pattern) > 500
        ):
            return False
        try:
            regex.compile(success_pattern)
            regex.compile(failure_pattern)
        except regex.error:
            return False
        stamp = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sent = db.execute(
                "SELECT status,destination_message FROM relay_deliveries "
                "WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone()
            if not sent or sent["status"] != "sent" or sent["destination_message"] != sent_message:
                return False
            result = db.execute(
                "INSERT OR IGNORE INTO relay_bot_results "
                "(destination,fingerprint,account_id,task_id,bot_chat,sent_message,"
                "success_pattern,failure_pattern,deadline,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    destination, fingerprint, account_id, task_id, bot_chat,
                    sent_message, success_pattern, failure_pattern,
                    stamp + timeout_minutes * 60, stamp, stamp,
                ),
            )
            return result.rowcount == 1

    def observe_bot_reply(
        self,
        *,
        account_id: int,
        bot_chat: int,
        reply_to_message: int,
        bot_message: int,
        text: str,
        stamp: int | None = None,
    ) -> dict | None:
        """Correlate only reply_to_message.id; never associate based on timing alone."""
        stamp = int(time.time()) if stamp is None else stamp
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute(
                "SELECT * FROM relay_bot_results WHERE account_id=? AND bot_chat=? "
                "AND sent_message=? AND deadline>=? "
                "AND status IN ('pending','unclassified')",
                (account_id, bot_chat, reply_to_message, stamp),
            ).fetchone()
            if not item:
                return None
            # A failure pattern takes precedence over success when both match.
            try:
                if regex.search(item["failure_pattern"], text, flags=regex.IGNORECASE, timeout=0.1):
                    status = "failed"
                elif regex.search(
                    item["success_pattern"], text, flags=regex.IGNORECASE, timeout=0.1
                ):
                    status = "succeeded"
                else:
                    status = "unclassified"
            except (TimeoutError, regex.error):
                return None
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            db.execute(
                "UPDATE relay_bot_results SET status=?,reply_message=?,reply_digest=?,"
                "updated_at=? WHERE destination=? AND fingerprint=? "
                "AND status IN ('pending','unclassified')",
                (
                    status, bot_message, digest, stamp,
                    item["destination"], item["fingerprint"],
                ),
            )
            return {
                "destination": item["destination"],
                "fingerprint": item["fingerprint"],
                "status": status,
                "task_id": item["task_id"],
            }

    def bot_results(
        self,
        *,
        account_id: int | None = None,
        task_id: int | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        if not 1 <= page <= 100000 or not 1 <= page_size <= 100:
            raise ValueError("invalid pagination")
        statuses = {"pending", "succeeded", "failed", "unclassified", "expired"}
        if status is not None and status not in statuses:
            raise ValueError("invalid bot result status")
        stamp = int(time.time())
        status_sql = (
            "CASE WHEN status IN ('pending','unclassified') AND deadline<? "
            "THEN 'expired' ELSE status END"
        )
        where, params = [], []
        if account_id is not None:
            where.append("account_id=?")
            params.append(account_id)
        if task_id is not None:
            where.append("task_id=?")
            params.append(task_id)
        if status:
            where.append(status_sql + "=?")
            params.extend((stamp, status))
        filter_sql = (" WHERE " + " AND ".join(where)) if where else ""
        # The computed status only needs one timestamp for selected rows.
        with self._connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM relay_bot_results" + filter_sql,
                params,
            ).fetchone()[0]
            rows = db.execute(
                "SELECT destination,fingerprint,account_id,task_id,bot_chat,sent_message,"
                "reply_message,deadline,created_at,updated_at,"
                + status_sql + " AS computed_status FROM relay_bot_results"
                + filter_sql + " ORDER BY updated_at DESC,rowid DESC LIMIT ? OFFSET ?",
                [stamp, *params, page_size, (page - 1) * page_size],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["status"] = item.pop("computed_status")
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def album_stats(self, account_id: int | None = None, task_id: int | None = None) -> dict:
        where = ["action IN ('album_forward','album_copy')"]
        args = []
        if account_id is not None:
            where.append("account_id=?")
            args.append(account_id)
        if task_id is not None:
            where.append("task_id=?")
            args.append(task_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT status,COUNT(*) AS count FROM relay_deliveries WHERE "
                + " AND ".join(where) + " GROUP BY status",
                args,
            ).fetchall()
        counts = {"sent": 0, "failed": 0, "sending": 0, "uncertain": 0}
        counts.update({row["status"]: row["count"] for row in rows})
        return counts
