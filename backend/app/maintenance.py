"""Opt-in bounded history retention and persistent, rate-limited relay alerts."""

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .db import Run, Session, get_setting, now, set_setting
from .events import bus
from .notifications import notify
from .relay_history import get_ledger
from .security import decrypt

router = APIRouter(prefix="/maintenance", tags=["运行维护"])
_cleanup_lock = threading.Lock()
BATCH_SIZE = 500


class MaintenancePolicy(BaseModel):
    enabled: bool = False
    run_days: int = Field(90, ge=7, le=3650)
    relay_sent_days: int = Field(90, ge=7, le=3650)
    relay_failed_days: int = Field(180, ge=30, le=3650)
    alert_enabled: bool = True
    recovery_enabled: bool = False
    alert_window_minutes: int = Field(5, ge=1, le=60)
    alert_cooldown_minutes: int = Field(30, ge=5, le=1440)


def policy() -> MaintenancePolicy:
    return MaintenancePolicy.model_validate_json(get_setting("maintenance_policy", "{}"))


def run_predicate(config):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.run_days)).isoformat()
    return (Run.status != "running") & Run.finished_at.is_not(None) & (Run.finished_at < cutoff)


def relay_cutoffs(config):
    stamp = int(time.time())
    return {
        "sent_before": stamp - config.relay_sent_days * 86400,
        "failed_before": stamp - config.relay_failed_days * 86400,
    }


@router.get("")
def maintenance_status():
    return {
        "policy": policy().model_dump(),
        "batch_size": BATCH_SIZE,
        "last_cleanup": json.loads(get_setting("last_cleanup", "null")),
        "alerts": get_ledger().alert_status(),
        "recovery_pending": get_setting("relay_recovery_pending", "0") == "1",
    }


@router.put("/policy")
def save_policy(config: MaintenancePolicy):
    set_setting("maintenance_policy", config.model_dump_json())
    return maintenance_status()


@router.get("/preview")
def preview_cleanup():
    config = policy()
    with Session() as db:
        runs = db.scalar(select(func.count(Run.id)).where(run_predicate(config)))
    return {
        "runs": runs,
        "relay": get_ledger().cleanup(**relay_cutoffs(config), dry_run=True),
        "batch_size": BATCH_SIZE,
    }


def cleanup_history():
    # Both manual requests and the scheduled job share this serialization guard.
    with _cleanup_lock:
        config = policy()
        if not config.enabled:
            raise HTTPException(409, "请先保存并启用历史清理策略")
        with Session() as db:
            ids = select(Run.id).where(run_predicate(config)).order_by(Run.id).limit(BATCH_SIZE)
            result = db.execute(delete(Run).where(Run.id.in_(ids)))
            runs = result.rowcount
            db.commit()
        relay = get_ledger().cleanup(**relay_cutoffs(config), limit=BATCH_SIZE)
        result = {"time": now(), "runs": runs, "relay": relay, "batch_size": BATCH_SIZE}
        set_setting("last_cleanup", json.dumps(result))
        return result


@router.post("/cleanup")
async def manual_cleanup():
    return await asyncio.to_thread(cleanup_history)


async def scheduled_cleanup():
    try:
        if await asyncio.to_thread(lambda: policy().enabled):
            await asyncio.to_thread(cleanup_history)
    except Exception as exc:
        bus.emit(f"历史清理未完成：{type(exc).__name__}", "WARNING")


async def check_relay_alerts():
    """Notifications run outside message workers; a provider failure cannot stop relay."""
    claim = None
    ledger = None
    try:
        config = await asyncio.to_thread(policy)
        if not config.alert_enabled:
            return
        channels = await asyncio.to_thread(
            lambda: json.loads(decrypt(get_setting("notifications")) or "[]")
        )
        if not any(c.get("enabled", True) for c in channels):
            return
        ledger = await asyncio.to_thread(get_ledger)
        claim = await asyncio.to_thread(
            ledger.claim_alert,
            since=int(time.time()) - config.alert_window_minutes * 60,
            cooldown=config.alert_cooldown_minutes * 60,
        )
        if not claim:
            return
        lines = [f"新增及积压的转发异常共 {claim['total']} 条（含失败和待核查）："]
        for group in claim["groups"]:
            lines.append(
                f"账号 #{group['account_id'] or '未知'} / 任务 #{group['task_id'] or '未知'}："
                f"{group['total']} 条 · {group['last_error'] or 'UnknownError'}"
            )
        lines.append("最多列出 10 组，请在 Dashboard「转发记录」筛选失败查看详情。")
        # notify has its own per-channel HTTP timeouts. Do not cancel the whole
        # batch after an earlier channel may already have delivered it.
        results = await notify("转发异常汇总", "\n".join(lines))
        # If any channel succeeds, do not duplicate alerts on the working channel.
        success = any(item.get("ok") for item in results)
        await asyncio.to_thread(ledger.complete_alert, claim["sequence"], success)
        if success:
            await asyncio.to_thread(set_setting, "relay_recovery_pending", "1")
        if not success:
            bus.emit("转发异常告警发送失败，请检查通知渠道；冷却后将重新检查。", "WARNING")
    except Exception as exc:
        if claim and ledger:
            await asyncio.to_thread(ledger.complete_alert, claim["sequence"], False)
        bus.emit(f"转发异常告警检查未完成：{type(exc).__name__}", "WARNING")


async def check_relay_recovery():
    """Opt-in one-shot notification after a previously alerted incident backlog clears.

    The durable pending flag is armed only after at least one failure alert
    was accepted by a provider. An offline check never claims success;
    delivery outcomes are read from the ledger, not from Telegram inference.
    """
    try:
        config = await asyncio.to_thread(policy)
        if not config.recovery_enabled:
            return
        if await asyncio.to_thread(get_setting, "relay_recovery_pending", "0") != "1":
            return
        from .relay_operations import current_fault_count
        if await asyncio.to_thread(current_fault_count):
            return
        results = await notify(
            "转发异常已恢复",
            "当前保留的转发记录中已没有失败或待核查项。此通知只表示故障账本已清空，"
            "不代表外部 Bot 已完成转存或第三方任务。",
        )
        if any(item.get("ok") for item in results):
            await asyncio.to_thread(set_setting, "relay_recovery_pending", "0")
    except Exception as exc:
        bus.emit(f"恢复通知检查未完成：{type(exc).__name__}", "WARNING")
