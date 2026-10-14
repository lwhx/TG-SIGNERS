# Relay Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Prevent ambiguous sends from being resent and add bounded opt-in retries for confirmed recoverable rejection.
**Architecture:** Extend the existing SQLite ledger with outcome/recovery fields and a focused recovery mixin. Reuse the source-verified replay path from the Dashboard and its existing scheduler; preserve one-container deployment.
**Tech Stack:** Python, SQLite, FastAPI, Kurigram, React/Next.js, pytest, Playwright.
**Spec:** `docs/P4-RELIABILITY.md`

## Global Constraints

- No new service/database; preserve accounts, tasks, sessions, data mount and successful dedup keys.
- Ambiguous sends and legacy unclassified failures must be reviewed before resending.
- Automatic retry only covers text/extract/template; forward/copy require manual handling.
- Automatic retry defaults off, maximum 3 processing attempts, 5 records per 30-second round; 24-hour eligibility.
- Exponential base 60 seconds, cap 3600 seconds; never shorten target or Telegram minimum delay.

## Review Focus

- SDK internal retries must not turn an ambiguous earlier success into a supposedly safe failure.
- Late send completion and administrator confirmation must not race into a second send.
- Crash between queue claim, source lookup and send must preserve both quota and uncertainty.
- Configuration changes and edited/deleted source messages must block queued sends.
- Disabled policies, row pauses and exhausted quotas must survive restarts and normal duplicate events.

### Task 1: Delivery classification and recovery state

Files: add `automation/delivery_outcome.py`, `automation/relay_recovery.py`; modify ledger/message_relay and backend Telegram client; add upstream and backend regression tests.
- [x] Write and observe failing tests: expired reservation is not sendable, timeout/cancel becomes uncertain, clear FloodWait can be retried, explicit confirmation preserves successful dedup.
- [x] Add `recover_stale()`, `resolve()`, classification and a context-limited single RPC invocation path; update history/stats and preserve migration compatibility.
- [x] Run focused tests and existing executor/backend suites.

### Task 2: Persistent bounded retry

Files: add `backend/app/relay_retry.py`; factor `replay_record()` from existing manual endpoint; add queue claim/control methods to recovery mixin; wire scheduler.
- [x] Write and observe failing tests for disabled default, claim concurrency, config mismatch, source edits, pause, quota, crash and delay floor.
- [x] Implement `claim_retry()`, `settle_retry()`, `control_retry()` and opt-in policy API; automatic replay receives an internal claim token, never a browser-supplied token.
- [x] Run complete Python suites and lint before integration.

### Task 3: Dashboard controls and delivery

Files: add settings recovery component; update relay-history status/filter/actions and tests; update CI/docs.
- [x] Write and observe failing browser tests for review-before-retry and saved retry settings.
- [x] Implement outcome explanations, confirmation actions, next retry/attempt count and pause controls.
- [x] Run typecheck, build, complete browser suite, Chinese-font mobile inspection and independent code review.
- [ ] Create PR, require CI success, squash merge and verify main CI/GHCR publication.

## Review fixes and evidence

Independent review findings were fixed with observed red/green regressions: active local sends block administrator resolution even after lease expiry, same-attempt late success overrides not_sent, full source identity detects media edits, snapshot copy avoids re-fetch, and cleaned failed rows leave suppression keys. Full Python: 249 passed; full browser: 17 passed. PR/CI/merge/publication completion is recorded by the GitHub PR and Actions, not inferred from local tests.
