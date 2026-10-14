"""Queue processing must be persistent, bounded and exclusive with other senders."""

import sqlite3
import time

import pytest

from tg_signer.automation.relay_ledger import RelayLedger


def failure(ledger, key="job", wait=1):
    ledger.reserve(
        "@target",
        key,
        "text",
        "-100",
        7,
        retry_after_seconds=0,
        account_id=1,
        task_id=2,
        rule_id="r",
        handler_index=0,
        target_index=0,
        part_index=0,
        dedup="message",
        payload_digest="a" * 64,
        config_digest="b" * 64,
        source_digest="c" * 64,
    )
    ledger.finish(
        "@target",
        key,
        1,
        False,
        "FloodWait",
        failure_kind="transient",
        retry_after_seconds=wait,
    )
    with sqlite3.connect(ledger.path) as db:
        db.execute(
            "UPDATE relay_deliveries SET next_retry_at=1,updated_at=?",
            (int(time.time()) - 10000,),
        )


def claim(ledger, **kwargs):
    return ledger.claim_retry(max_attempts=kwargs.get("max_attempts", 3), since=0)


def test_claim_is_exclusive_and_consumes_persistent_quota(tmp_path):
    ledger = RelayLedger(tmp_path)
    failure(ledger)
    first = claim(ledger)
    assert first and first["auto_attempts"] == 1
    assert claim(RelayLedger(tmp_path)) is None
    assert ledger.reserve("@target", "job", "text", "-100", 7, force_failed=True) == (
        "retry_claimed",
        None,
    )
    assert ledger.reserve(
        "@target",
        "job",
        "text",
        "-100",
        7,
        force_failed=True,
        retry_claim=first["claim_token"],
    ) == ("reserved", 2)
    ledger.finish("@target", "job", 2, False, "FloodWait", failure_kind="transient")
    ledger.settle_retry(first, max_attempts=1, base_delay=60, max_delay=3600)
    row = ledger.get("@target", "job")
    assert row["retry_status"] == "exhausted" and row["next_retry_at"] is None
    assert claim(ledger, max_attempts=1) is None


def test_pause_resume_and_minimum_delay(tmp_path):
    ledger = RelayLedger(tmp_path)
    failure(ledger, wait=9000)
    assert ledger.control_retry("@target", "job", 1, True)
    assert claim(ledger) is None
    assert ledger.control_retry("@target", "job", 1, False)
    picked = claim(ledger)
    assert picked
    ledger.settle_retry(picked, max_attempts=3, base_delay=60, max_delay=3600)
    row = ledger.get("@target", "job")
    assert row["next_retry_at"] - row["updated_at"] >= 9000


def test_claim_crash_recovers_only_before_send(tmp_path):
    ledger = RelayLedger(tmp_path)
    failure(ledger)
    first = claim(ledger)
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE relay_deliveries SET retry_claim_until=1")
    second = claim(RelayLedger(tmp_path))
    assert second and second["claim_token"] != first["claim_token"]
    assert second["auto_attempts"] == 2
    assert (
        ledger.reserve(
            "@target",
            "job",
            "text",
            "-100",
            7,
            force_failed=True,
            retry_claim=first["claim_token"],
        )[0]
        == "claim_expired"
    )
    assert (
        ledger.reserve(
            "@target",
            "job",
            "text",
            "-100",
            7,
            force_failed=True,
            retry_claim=second["claim_token"],
        )[0]
        == "reserved"
    )
    ledger.recover_stale(all_sending=True)
    assert claim(ledger) is None
    assert ledger.get("@target", "job")["status"] == "uncertain"


def test_stop_preflight_failure_and_expire_old_queue(tmp_path):
    ledger = RelayLedger(tmp_path)
    failure(ledger)
    picked = claim(ledger)
    ledger.settle_retry(
        picked,
        max_attempts=3,
        base_delay=60,
        max_delay=3600,
        stop_reason="SourceChanged",
    )
    assert ledger.get("@target", "job")["retry_status"] == "blocked"
    assert claim(ledger) is None
    failure(ledger, "old")
    ledger.expire_retries(since=2**40, max_attempts=3)
    assert ledger.get("@target", "old")["retry_status"] == "expired"


@pytest.mark.parametrize("action", ["forward", "copy"])
def test_media_actions_cannot_be_claimed_even_with_old_schedule(tmp_path, action):
    ledger = RelayLedger(tmp_path)
    failure(ledger)
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE relay_deliveries SET action=?", (action,))
    assert claim(ledger) is None


def test_cleanup_preserves_review_and_retry_suppression(tmp_path):
    ledger = RelayLedger(tmp_path)
    failure(ledger, "legacy")
    failure(ledger, "exhausted")
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE relay_deliveries SET next_retry_at=NULL,updated_at=1")
        db.execute(
            "UPDATE relay_deliveries SET failure_kind=NULL WHERE fingerprint='legacy'"
        )
        db.execute(
            "UPDATE relay_deliveries SET retry_status='exhausted' WHERE fingerprint='exhausted'"
        )
    assert ledger.cleanup(sent_before=100, failed_before=100) == 1
    assert ledger.get("@target", "legacy")["needs_review"]
    reopened = RelayLedger(tmp_path)
    assert (
        reopened.reserve("@target", "exhausted", "text", "-100", 7)[0]
        == "history_removed"
    )
