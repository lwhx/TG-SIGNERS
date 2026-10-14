"""P5 read-only incident visibility and bounded, metadata-only relay CSV export.

The delivery ledger remains the single source of truth; these endpoints never
claim retries, change delivery states, or infer successful Telegram delivery.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from .db import Run, Session, Task
from .engine import task_engine
from .relay_history import get_ledger
from .relay_retry import retry_policy
from .telegram import telegram

router = APIRouter(prefix="/relay", tags=["P5 故障中心与导出"])

_EXPORT_FIELDS = (
    "updated_at", "created_at", "status", "account_id", "task_id",
    "rule_id", "source_chat", "source_message", "destination",
    "destination_message", "action", "attempts", "auto_attempts",
    "failure_kind", "retry_status", "retry_paused", "next_retry_at",
    "last_error", "last_retry_error", "resolution", "fingerprint",
)


def _conditions(account_id=None, task_id=None, status=None, from_ts=None, to_ts=None):
    where, params = [], []
    if account_id is not None:
        where.append("account_id=?")
        params.append(account_id)
    if task_id is not None:
        where.append("task_id=?")
        params.append(task_id)
    if status is not None:
        where.append("status=?")
        params.append(status)
    if from_ts is not None:
        where.append("updated_at>=?")
        params.append(from_ts)
    if to_ts is not None:
        where.append("updated_at<=?")
        params.append(to_ts)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def current_fault_count():
    """All currently retained failed / uncertain deliveries, not just recent ones."""
    with get_ledger()._connect() as db:
        return int(db.execute(
            "SELECT COUNT(*) FROM relay_deliveries WHERE status IN ('failed','uncertain')"
        ).fetchone()[0])


def listener_snapshot():
    """Distinguish a configured task from a live registered listener."""
    with Session() as db:
        tasks = list(db.scalars(
            select(Task).where(Task.kind.in_(("automation", "monitor"))).order_by(Task.id.desc())
        ))
        latest = dict(db.execute(
            select(Run.task_id, func.max(Run.finished_at))
            .where(Run.task_id.is_not(None), Run.finished_at.is_not(None))
            .group_by(Run.task_id)
        ).all())
    result = []
    for task in tasks:
        future = task_engine.running.get(task.id)
        active = future is not None and not future.done()
        client = telegram.clients.get(task.account_id)
        connected = bool(client and getattr(client, "is_connected", False))
        restarting = task_engine.scheduler.get_job(f"task-{task.id}") is not None
        if not task.enabled:
            state = "disabled"
        elif active and task.id in task_engine.listener_ready and connected:
            state = "listening"
        elif active and task.id in task_engine.listener_ready:
            state = "disconnected"
        elif active:
            state = "starting"
        elif restarting:
            state = "restarting"
        else:
            state = "offline"
        result.append({
            "task_id": task.id,
            "task_name": task.name,
            "account_id": task.account_id,
            "kind": task.kind,
            "state": state,
            "enabled": task.enabled,
            "last_status": task.last_status,
            "last_run": task.last_run,
            "last_event": latest.get(task.id),
        })
    return result


@router.get("/operations", summary="故障中心、重试概览和实时监听快照")
def relay_operations(
    account_id: int | None = Query(None, ge=1),
    task_id: int | None = Query(None, ge=1),
):
    ledger = get_ledger()
    suffix, params = _conditions(account_id, task_id)
    failed_filter = "status IN ('failed','uncertain')"
    fault_where = suffix + (" AND " if suffix else " WHERE ") + failed_filter
    stamp = int(time.time())
    with ledger._connect() as db:
        status_rows = db.execute(
            "SELECT status,COUNT(*) AS total FROM relay_deliveries"
            + suffix + " GROUP BY status", params
        ).fetchall()
        retry_suffix = suffix + (" AND " if suffix else " WHERE ") + "status='failed'"
        retry_rows = db.execute(
            "SELECT retry_status,retry_paused,COUNT(*) AS total,"
            "MIN(next_retry_at) AS next_at FROM relay_deliveries"
            + retry_suffix + " GROUP BY retry_status,retry_paused", params
        ).fetchall()
        due = db.execute(
            "SELECT COUNT(*) FROM relay_deliveries" + retry_suffix
            + " AND retry_status IN ('scheduled','claimed') AND retry_paused=0"
            " AND next_retry_at<=? AND COALESCE(retry_claim_until,0)<=?",
            [*params, stamp, stamp],
        ).fetchone()[0]
        groups = db.execute(
            "SELECT account_id,task_id,status,"
            "COALESCE(failure_kind,'legacy') AS failure_kind,"
            "COALESCE(last_error,'UnknownError') AS last_error,"
            "COUNT(*) AS total, MAX(updated_at) AS last_at "
            "FROM relay_deliveries" + fault_where
            + " GROUP BY account_id,task_id,status,failure_kind,last_error"
            " ORDER BY last_at DESC LIMIT 25",
            params,
        ).fetchall()
    counts = {"sent": 0, "failed": 0, "uncertain": 0, "sending": 0}
    counts.update({r["status"]: r["total"] for r in status_rows})
    retry_counts = {
        "scheduled": 0, "claimed": 0, "paused": 0, "manual_only": 0,
        "blocked": 0, "exhausted": 0, "expired": 0,
    }
    next_at = None
    for r in retry_rows:
        label = r["retry_status"] or "blocked"
        if label in retry_counts:
            retry_counts[label] += r["total"]
        if r["retry_paused"] and label in {"scheduled", "claimed"}:
            retry_counts["paused"] += r["total"]
        if not r["retry_paused"] and label in {"scheduled", "claimed"} and r["next_at"]:
            next_at = min(next_at, r["next_at"]) if next_at else r["next_at"]
    enabled = retry_policy().enabled
    listeners = listener_snapshot()
    if account_id is not None:
        listeners = [item for item in listeners if item["account_id"] == account_id]
    if task_id is not None:
        listeners = [item for item in listeners if item["task_id"] == task_id]
    return {
        "observed_at": stamp,
        "faults": {
            **counts,
            "total": counts["failed"] + counts["uncertain"],
            "groups": [{**dict(r), "last_error": str(r["last_error"])[:180]} for r in groups],
        },
        "retries": {
            **retry_counts, "enabled": enabled, "due": due, "next_at": next_at,
        },
        "listeners": listeners,
    }


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    cell = str(value).replace("\r", " ").replace("\n", " ").replace("\x00", "")[:1000]
    # Excel/Sheets treat CSV cells beginning with these symbols as formulas.
    if cell.lstrip().startswith(("=", "+", "-", "@", "\t")):
        cell = "'" + cell
    return cell


@router.get("/export", summary="导出符合筛选条件的投递元数据 CSV（最多一万条）")
def export_relay_history(
    status: str | None = Query(None, pattern=r"^(sent|failed|sending|uncertain)$"),
    account_id: int | None = Query(None, ge=1),
    task_id: int | None = Query(None, ge=1),
    from_ts: int | None = Query(None, ge=0),
    to_ts: int | None = Query(None, ge=0),
    limit: int = Query(5000, ge=1, le=10000),
):
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(422, "起始时间不能大于结束时间")
    where, params = _conditions(account_id, task_id, status, from_ts, to_ts)
    columns = ",".join(_EXPORT_FIELDS)
    with get_ledger()._connect() as db:
        rows = db.execute(
            f"SELECT {columns} FROM relay_deliveries" + where
            + " ORDER BY updated_at DESC,rowid DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(_EXPORT_FIELDS)
    for item in rows:
        writer.writerow([_csv_cell(item[field]) for field in _EXPORT_FIELDS])
    filename = datetime.now(timezone.utc).strftime("relay-records-%Y%m%d-%H%M%S.csv")
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Count": str(len(rows)),
            "X-Export-Limit": str(limit),
        },
    )
