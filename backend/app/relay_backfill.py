"""P7 manual, bounded, restart-safe Telegram history backfill for relay-only rules.

Never executes arbitrary automation handlers. Never overrides the persistent
P4 relay idempotency ledger. An interrupted batch must be manually restarted
with its ORIGINAL offset; no automatic resume or replay of uncertain sends.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from tg_signer.automation.delivery_outcome import PreSendRejected, configuration_digest
from tg_signer.automation.engine import UserAutomation
from tg_signer.automation.message_relay import relay_message
from tg_signer.automation.models import AutomationContext, Event
from tg_signer.config import AutomationConfig

from .db import Account, RelayBackfill, Session, Task, now
from .engine import task_engine
from .security import rate_limit
from .telegram import telegram

router = APIRouter(prefix="/relay/backfill", tags=["P7 历史消息补抓"])
_jobs: dict[int, asyncio.Task] = {}
_lock = asyncio.Lock()
logger = logging.getLogger("tg-signer")


class BackfillRequest(BaseModel):
    task_id: int = Field(ge=1)
    source_chat: str = Field(min_length=1, max_length=255)
    limit: int = Field(default=20, ge=1, le=50)
    before_id: int | None = Field(default=None, gt=0)
    confirmation: Literal["BACKFILL"] | None = None


def _normalize_chat(value) -> str:
    return str(value).strip().lower().lstrip("@")


def _explicit_trigger_sources(rule: dict) -> set[str]:
    result = set()
    for trigger in rule.get("triggers", []):
        if trigger.get("type") != "message":
            continue
        params = trigger.get("params") or {}
        items = params.get("chat_ids") or []
        if params.get("chat_id") is not None:
            items = [*items, params["chat_id"]]
        result.update(_normalize_chat(item) for item in items)
    return result


def _plan(data: BackfillRequest) -> tuple[dict, int, list[dict]]:
    with Session() as db:
        task = db.get(Task, data.task_id)
        if not task or task.kind != "automation" or not task.enabled:
            raise HTTPException(409, "只能补抓当前已启用的自动化转发任务")
        if not db.get(Account, task.account_id):
            raise HTTPException(409, "对应 Telegram 账号不存在")
        config = task.config
        account_id = task.account_id
    cfg = AutomationConfig.model_validate(config).model_dump(mode="json")
    requested = _normalize_chat(data.source_chat)
    rules = [
        r for r in cfg.get("rules", [])
        if r.get("enabled", True) and requested in _explicit_trigger_sources(r)
        and any(h.get("handler") == "relay_message" for h in r.get("handlers", []))
    ]
    if not rules:
        raise HTTPException(
            422, "补抓来源必须与至少一条已启用转发规则的消息触发器来源完全一致"
        )
    for rule in rules:
        for handler in rule.get("handlers", []):
            # No AI, send_text, external APIs or stateful handlers on history.
            if handler.get("handler") != "relay_message":
                raise HTTPException(422, "补抓只支持纯 relay_message 规则，禁止执行其他动作")
            params = handler.get("params") or {}
            if params.get("ignore_self", True) is False:
                raise HTTPException(422, "补抓规则必须忽略自己发送的消息")
            for target in params.get("targets") or []:
                if target.get("dedup", "message") == "off":
                    raise HTTPException(422, "历史补抓要求启用去重，禁止关闭去重的目标")
    return cfg, account_id, rules


def _job_dict(job: RelayBackfill) -> dict:
    return {
        key: getattr(job, key) for key in (
            "id", "task_id", "account_id", "source_chat", "before_id",
            "next_before_id", "batch_size", "status", "scanned",
            "matched", "last_error", "created_at", "updated_at",
        )
    }


def recover_backfills() -> None:
    """Mark work from an earlier process as interrupted, never resume blindly."""
    with Session() as db:
        db.execute(
            update(RelayBackfill)
            .where(RelayBackfill.status.in_(("queued", "running", "cancel_requested")))
            .values(status="interrupted", updated_at=now(), last_error="ServiceRestarted")
        )
        db.commit()


def _set_job(job_id: int, **values):
    with Session() as db:
        job = db.get(RelayBackfill, job_id)
        if not job:
            return
        for key, value in values.items():
            setattr(job, key, value)
        job.updated_at = now()
        db.commit()


def _check_unchanged(task_id: int, account_id: int, config_digest: str, job_id: int):
    with Session() as db:
        task = db.get(Task, task_id)
        job = db.get(RelayBackfill, job_id)
        if (
            not task or not task.enabled or task.kind != "automation"
            or task.account_id != account_id or not job
            or job.status != "running"
            or configuration_digest(AutomationConfig.model_validate(task.config)) != config_digest
        ):
            raise PreSendRejected("BackfillTaskChanged")


def _message_in_source(message, source: str) -> bool:
    chat = getattr(message, "chat", None)
    if chat is None:
        return False
    source = _normalize_chat(source)
    return (
        _normalize_chat(getattr(chat, "id", None)) == source
        or _normalize_chat(getattr(chat, "username", None)) == source
    )


async def _execute(
    job_id: int, account_id: int, task_id: int,
    source: str, limit: int, before_id: int | None, cfg: dict, selected: list[dict],
):
    scanned = 0
    matched = 0
    try:
        _set_job(job_id, status="running")
        expected_digest = configuration_digest(AutomationConfig.model_validate(cfg))
        worker = await telegram.worker(account_id, UserAutomation, task_id)
        worker.account_id = account_id
        worker.config = AutomationConfig.model_validate(cfg)
        worker.relay_before_send = lambda: _check_unchanged(
            task_id, account_id, expected_digest, job_id
        )
        source_id = int(source) if source.lstrip("-").isdecimal() else source
        messages = []
        async with asyncio.timeout(600):
            async for item in worker.app.get_chat_history(
                source_id, limit=limit, offset_id=before_id or 0
            ):
                if getattr(item, "empty", False) or not getattr(item, "id", None):
                    continue
                if not _message_in_source(item, source):
                    raise ValueError("SourceMismatch")
                if before_id is not None and item.id >= before_id:
                    raise ValueError("HistoryOffsetMismatch")
                messages.append(item)
                if len(messages) >= limit:
                    break
            messages.sort(key=lambda item: item.id)
            for message in messages:
                _check_unchanged(task_id, account_id, expected_digest, job_id)
                relevant = 0
                for rule in selected:
                    rule_obj = next(
                        r for r in worker.config.rules if r.id == rule["id"] and r.enabled
                    )
                    triggers = [
                        trigger for trigger in rule_obj.triggers
                        if trigger.type == "message"
                        and worker._match_message_trigger(trigger, message)
                    ]
                    if not triggers:
                        continue
                    if rule_obj.filters and not worker._match_filter(rule_obj.filters, message):
                        continue
                    relevant += 1
                    for trigger in triggers:
                        for handler_index, handler in enumerate(rule_obj.handlers):
                            event = Event(
                                type="message",
                                chat_id=message.chat.id,
                                message=message,
                                now=datetime.now(timezone.utc),
                                trigger_id=trigger.id or "backfill",
                                rule_id=rule_obj.id,
                            )
                            context = AutomationContext(
                                vars={
                                    **rule_obj.vars,
                                    **worker.state.get_rule_vars(rule_obj.id),
                                },
                                state=worker.state,
                                client=worker.app,
                                logger=logger,
                                worker=worker,
                                workdir=worker.workdir,
                            )
                            context.handler_index = handler_index
                            outcome = await relay_message(event, context, handler.params or {})
                            if outcome == "stop":
                                raise RuntimeError("RelayDeliveryFailed")
                scanned += 1
                if relevant:
                    matched += 1
                _set_job(job_id, scanned=scanned, matched=matched)
                await asyncio.sleep(1)
        _set_job(
            job_id, status="completed", scanned=scanned, matched=matched,
            next_before_id=min(m.id for m in messages) if messages else None,
        )
    except asyncio.CancelledError:
        _set_job(
            job_id, status="interrupted", scanned=scanned,
            matched=matched, last_error="Cancelled",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        _set_job(
            job_id, status="failed", scanned=scanned,
            matched=matched, last_error=type(exc).__name__,
        )
        logger.warning("History backfill #%s failed: %s", job_id, type(exc).__name__)


@router.post("/preview")
def preview_backfill(data: BackfillRequest):
    cfg, account_id, rules = _plan(data)
    return {
        "task_id": data.task_id,
        "account_id": account_id,
        "source_chat": data.source_chat,
        "limit": data.limit,
        "before_id": data.before_id,
        "rules": [item["id"] for item in rules],
        "target_count": sum(
            len(handler.get("params", {}).get("targets") or [])
            for rule in rules for handler in rule["handlers"]
        ),
        "dry_run": True,
        "warning": (
            "仅检查配置，不读取或发送消息。手动开始后只执行通用转发动作，"
            "每批最多 50 条，按旧到新处理，任何已投递或待核查消息由去重账本阻止再次发送。"
        ),
    }


@router.post("/start", status_code=202)
async def start_backfill(data: BackfillRequest, request: Request):
    if data.confirmation != "BACKFILL":
        raise HTTPException(400, "请先预览并明确确认历史补抓")
    rate_limit(
        "relay-backfill:" + (request.client.host if request.client else "local"),
        limit=5, window=60,
    )
    async with _lock:
        if task_engine.stopping:
            raise HTTPException(503, "服务正在关闭")
        cfg, account_id, rules = _plan(data)
        if any(not future.done() for future in _jobs.values()):
            raise HTTPException(409, "已有历史补抓正在运行，完成或中止后再启动下一批")
        with Session() as db:
            job = RelayBackfill(
                task_id=data.task_id, account_id=account_id, source_chat=data.source_chat,
                batch_size=data.limit, before_id=data.before_id,
            )
            db.add(job)
            db.commit()
            job_id = job.id
        future = asyncio.create_task(
            _execute(
                job_id, account_id, data.task_id, data.source_chat,
                data.limit, data.before_id, cfg, rules,
            ),
            name=f"relay-backfill-{job_id}",
        )
        _jobs[job_id] = future
        future.add_done_callback(lambda _: _jobs.pop(job_id, None))
        return {"id": job_id, "status": "queued"}


@router.get("/jobs")
def backfill_jobs(
    task_id: int | None = Query(None, ge=1),
    limit: int = Query(20, ge=1, le=50),
):
    with Session() as db:
        query = select(RelayBackfill)
        if task_id is not None:
            query = query.where(RelayBackfill.task_id == task_id)
        records = db.scalars(query.order_by(RelayBackfill.id.desc()).limit(limit)).all()
        return {"items": [_job_dict(row) for row in records]}


@router.post("/jobs/{job_id}/cancel")
async def cancel_backfill(job_id: int):
    async with _lock:
        with Session() as db:
            job = db.get(RelayBackfill, job_id)
            if not job:
                raise HTTPException(404, "历史补抓记录不存在")
            if job.status not in {"running", "queued", "cancel_requested"}:
                raise HTTPException(409, "该批次已结束，无法停止")
            job.status = "cancel_requested"
            db.commit()
        future = _jobs.get(job_id)
        if future and not future.done():
            future.cancel()
            await asyncio.gather(future, return_exceptions=True)
        else:
            _set_job(job_id, status="interrupted", last_error="Cancelled")
        return {"status": "interrupted", "id": job_id}


async def stop_all_backfills():
    async with _lock:
        futures = [future for future in _jobs.values() if not future.done()]
        for future in futures:
            future.cancel()
        if futures:
            await asyncio.gather(*futures, return_exceptions=True)
