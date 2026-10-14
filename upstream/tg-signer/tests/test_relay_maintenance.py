"""Retention must never make a successful delivery sendable again."""

import sqlite3

from tg_signer.automation.relay_ledger import RelayLedger


def seed(ledger, key, status, age=200, dedup="message"):
    ledger.reserve(
        "@target", key, "text", "-100", 1, dedup=dedup, task_id=3, account_id=4
    )
    if status != "sending":
        ledger.finish("@target", key, 1, status == "sent", "FloodWait")
    with sqlite3.connect(ledger.path) as db:
        db.execute(
            "UPDATE relay_deliveries SET updated_at=? WHERE fingerprint=?",
            (2_000_000_000 - age * 86400, key),
        )


def test_cleanup_preview_bounded_and_preserves_idempotency(tmp_path):
    ledger = RelayLedger(tmp_path)
    for key, status, age, dedup in [
        ("sent", "sent", 200, "content"),
        ("legacy", "sent", 200, None),
        ("off", "sent", 200, "off"),
        ("failed", "failed", 200, "message"),
        ("sending", "sending", 200, "message"),
        ("fresh", "sent", 1, "message"),
    ]:
        seed(ledger, key, status, age, dedup)
    assert (
        ledger.cleanup(
            sent_before=2_000_000_000 - 90 * 86400,
            failed_before=2_000_000_000 - 180 * 86400,
            dry_run=True,
        )
        == 4
    )
    assert ledger.history()["total"] == 6
    assert (
        ledger.cleanup(
            sent_before=2_000_000_000 - 90 * 86400,
            failed_before=2_000_000_000 - 180 * 86400,
            limit=2,
        )
        == 2
    )
    assert ledger.history()["total"] == 4
    ledger.cleanup(
        sent_before=2_000_000_000 - 90 * 86400,
        failed_before=2_000_000_000 - 180 * 86400,
    )
    assert {r["fingerprint"] for r in ledger.history()["items"]} == {"sending", "fresh"}
    reopened = RelayLedger(tmp_path)
    for key in ["sent", "legacy"]:
        assert reopened.reserve("@target", key, "text", "-100", 1) == (
            "duplicate",
            None,
        )
        assert reopened.reserve(
            "@target", key, "text", "-100", 1, force_failed=True
        ) == ("duplicate", None)
    assert reopened.reserve("@target", "off", "text", "-100", 1) == ("reserved", 1)


def test_alert_claim_persists_cooldown_and_acknowledges_only_success(tmp_path):
    ledger = RelayLedger(tmp_path)
    seed(ledger, "failure", "failed", 0)
    # Retry failure is a new event even within the same second.
    claim = ledger.claim_alert(since=0, cooldown=1800, stamp=2_000_000_001)
    assert claim["total"] == 1
    assert claim["groups"][0]["task_id"] == 3
    assert (
        RelayLedger(tmp_path).claim_alert(since=0, cooldown=1800, stamp=2_000_000_002)
        is None
    )
    ledger.complete_alert(claim["sequence"], False)
    again = ledger.claim_alert(since=0, cooldown=1800, stamp=2_000_001_802)
    assert again["total"] == 1
    ledger.complete_alert(again["sequence"], True)
    assert ledger.claim_alert(since=0, cooldown=1800, stamp=2_000_003_603) is None
    ledger.reserve("@target", "failure", "text", "-100", 1, force_failed=True)
    ledger.finish("@target", "failure", 2, False, "FloodWait")
    assert ledger.claim_alert(since=0, cooldown=1800, stamp=2_000_003_604)["total"] == 1


def test_unfiltered_recent_history_uses_index(tmp_path):
    ledger = RelayLedger(tmp_path)
    with sqlite3.connect(ledger.path) as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM relay_deliveries "
            "ORDER BY updated_at DESC, rowid DESC LIMIT 20"
        ).fetchall()
    assert not any("TEMP B-TREE" in str(row) for row in plan)


def test_failures_during_cooldown_survive_short_lookback_window(tmp_path):
    ledger = RelayLedger(tmp_path)
    seed(ledger, "first", "failed", 0)
    first = ledger.claim_alert(since=0, cooldown=1800, stamp=2_000_000_001)
    ledger.complete_alert(first["sequence"], True)
    seed(ledger, "during-cooldown", "failed", 0)
    later = ledger.claim_alert(since=2_000_001_502, cooldown=1800, stamp=2_000_001_802)
    assert later is not None
    assert later["total"] == 1


def test_failed_initial_notification_does_not_include_old_disabled_history(tmp_path):
    ledger = RelayLedger(tmp_path)
    seed(ledger, "old-disabled", "failed", 200)
    seed(ledger, "new", "failed", 0)
    first = ledger.claim_alert(
        since=2_000_000_000 - 300, cooldown=1800, stamp=2_000_000_001
    )
    assert first["total"] == 1
    ledger.complete_alert(first["sequence"], False)
    retried = RelayLedger(tmp_path).claim_alert(
        since=2_000_001_502, cooldown=1800, stamp=2_000_001_802
    )
    assert retried["total"] == 1
