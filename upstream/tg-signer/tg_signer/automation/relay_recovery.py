"""SQLite recovery transitions; an expired send is never a resend permission."""

import time
from contextlib import contextmanager
from threading import RLock

_active_lock = RLock()
_active_sends = set()


class RelayRecoveryMixin:
    def _active_key(self, destination, fingerprint):
        return (str(self.path.resolve()), destination, fingerprint)

    @contextmanager
    def active_delivery(self, destination, fingerprint):
        """Keep live local RPCs protected even if their persistent lease expires."""
        key = self._active_key(destination, fingerprint)
        with _active_lock:
            acquired = key not in _active_sends
            if acquired:
                _active_sends.add(key)
        try:
            yield acquired
        finally:
            if acquired:
                with _active_lock:
                    _active_sends.discard(key)

    @staticmethod
    def _next_failure_sequence(db):
        db.execute("UPDATE relay_alert_state SET sequence=sequence+1 WHERE id=1")
        return db.execute(
            "SELECT sequence FROM relay_alert_state WHERE id=1"
        ).fetchone()[0]

    def _mark_uncertain(self, db, destination, fingerprint, reason):
        db.execute(
            "UPDATE relay_deliveries SET status='uncertain', failure_kind='unknown', "
            "updated_at=?,last_error=?,failure_seq=?,next_retry_at=NULL, "
            "retry_status='blocked',retry_claim=NULL,retry_claim_until=NULL "
            "WHERE destination=? AND fingerprint=? AND status='sending'",
            (
                int(time.time()),
                reason,
                self._next_failure_sequence(db),
                destination,
                fingerprint,
            ),
        )

    def recover_stale(self, *, all_sending=False, limit=500):
        if not 1 <= limit <= 5000:
            raise ValueError("invalid recovery batch size")
        stamp = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            predicate = (
                "" if all_sending else " AND COALESCE(lease_until,updated_at+300)<=?"
            )
            params = [] if all_sending else [stamp]
            rows = db.execute(
                "SELECT destination,fingerprint FROM relay_deliveries WHERE status='sending'"
                + predicate
                + " LIMIT ?",
                [*params, limit],
            ).fetchall()
            for row in rows:
                self._mark_uncertain(
                    db, row["destination"], row["fingerprint"], "InterruptedSend"
                )
        return len(rows)

    def resolve(self, destination, fingerprint, attempt, decision):
        if decision not in {"sent", "not_sent"}:
            raise ValueError("invalid review decision")
        stamp = int(time.time())
        with _active_lock, self._connect() as db:
            if self._active_key(destination, fingerprint) in _active_sends:
                return False
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM relay_deliveries WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone()
            if (
                not row
                or row["attempts"] != attempt
                or not self._as_item(row)["needs_review"]
                or (row["lease_until"] or 0) > stamp
            ):
                return False
            db.execute(
                "INSERT INTO relay_reviews VALUES(?,?,?,?,?)",
                (destination, fingerprint, attempt, decision, stamp),
            )
            db.execute(
                "UPDATE relay_deliveries SET status=?, failure_kind=?, resolution=?,resolved_at=?, "
                "updated_at=?, next_retry_at=NULL,retry_status='none',retry_paused=1, "
                "retry_claim=NULL,retry_claim_until=NULL WHERE destination=? AND fingerprint=?",
                (
                    "sent" if decision == "sent" else "failed",
                    "reviewed",
                    decision,
                    stamp,
                    stamp,
                    destination,
                    fingerprint,
                ),
            )
            return True

    def claim_retry(self, *, max_attempts, since, base_delay=60):
        from secrets import token_hex

        stamp = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM relay_deliveries WHERE status='failed' AND failure_kind='transient' "
                "AND action IN ('text','extract','template') AND source_digest IS NOT NULL "
                "AND retry_paused=0 AND retry_status IN ('scheduled','claimed') "
                "AND next_retry_at<=? AND updated_at>=? AND updated_at+?<=? AND auto_attempts<? "
                "AND COALESCE(retry_claim_until,0)<=? AND config_digest IS NOT NULL "
                "AND account_id IS NOT NULL AND task_id IS NOT NULL AND source_message IS NOT NULL "
                "AND rule_id IS NOT NULL AND handler_index IS NOT NULL AND target_index IS NOT NULL "
                "AND part_index IS NOT NULL AND payload_digest IS NOT NULL "
                "ORDER BY next_retry_at,rowid LIMIT 1",
                (stamp, since, base_delay, stamp, max_attempts, stamp),
            ).fetchone()
            if not row:
                return None
            token = token_hex(24)
            db.execute(
                "UPDATE relay_deliveries SET retry_claim=?,retry_claim_until=?, "
                "auto_attempts=auto_attempts+1,retry_status='claimed' "
                "WHERE destination=? AND fingerprint=?",
                (token, stamp + 120, row["destination"], row["fingerprint"]),
            )
            result = self._as_item(row)
            result.update(claim_token=token, auto_attempts=row["auto_attempts"] + 1)
            return result

    def settle_retry(
        self, claim, *, max_attempts, base_delay, max_delay, stop_reason=None
    ):
        stamp = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM relay_deliveries WHERE destination=? AND fingerprint=? "
                "AND retry_claim=? AND status!='sending'",
                (claim["destination"], claim["fingerprint"], claim["claim_token"]),
            ).fetchone()
            if not row:
                return
            status, due = "none", None
            if row["status"] == "failed" and row["action"] in {"forward", "copy"}:
                status = "manual_only"
            elif row["status"] == "failed" and row["failure_kind"] == "transient":
                if stop_reason:
                    status = "blocked"
                elif row["auto_attempts"] >= max_attempts:
                    status = "exhausted"
                else:
                    status = "scheduled"
                    delay = max(
                        row["retry_floor"],
                        min(max_delay, base_delay * 2 ** row["auto_attempts"]),
                    )
                    due = max(stamp + delay, row["next_retry_at"] or 0)
            elif row["status"] != "sent":
                status = "blocked"
            db.execute(
                "UPDATE relay_deliveries SET retry_status=?,next_retry_at=?,last_retry_error=?, "
                "retry_claim=NULL,retry_claim_until=NULL WHERE destination=? AND fingerprint=?",
                (status, due, stop_reason, row["destination"], row["fingerprint"]),
            )

    def control_retry(self, destination, fingerprint, attempt, paused):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM relay_deliveries WHERE destination=? AND fingerprint=?",
                (destination, fingerprint),
            ).fetchone()
            if (
                not row
                or row["status"] != "failed"
                or row["failure_kind"] != "transient"
                or row["attempts"] != attempt
                or row["retry_status"] not in {"scheduled", "claimed"}
                or (row["retry_claim_until"] or 0) > int(time.time())
            ):
                return False
            db.execute(
                "UPDATE relay_deliveries SET retry_paused=? WHERE destination=? AND fingerprint=?",
                (int(paused), destination, fingerprint),
            )
            return True

    def expire_retries(self, *, since, max_attempts):
        stamp = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE relay_deliveries SET retry_status=CASE WHEN updated_at<? THEN 'expired' ELSE 'exhausted' END, "
                "next_retry_at=NULL,retry_claim=NULL,retry_claim_until=NULL "
                "WHERE status='failed' AND next_retry_at IS NOT NULL AND COALESCE(retry_claim_until,0)<=? "
                "AND (updated_at<? OR auto_attempts>=?)",
                (since, stamp, since, max_attempts),
            )
            db.execute(
                "UPDATE relay_deliveries SET retry_claim=NULL,retry_claim_until=NULL "
                "WHERE status!='sending' AND COALESCE(retry_claim_until,0)>0 AND retry_claim_until<=?",
                (stamp,),
            )
