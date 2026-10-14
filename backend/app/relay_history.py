"""Authenticated relay history and source-verified manual retry APIs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from tg_signer.automation.engine import UserAutomation
from tg_signer.automation.message_relay import relay_message
from tg_signer.automation.models import AutomationContext, Event
from tg_signer.automation.relay_ledger import RelayLedger
from tg_signer.config import AutomationConfig

from .db import DATA, Account, Session, Task
from .security import rate_limit
from .telegram import telegram

router = APIRouter(prefix="/relay", tags=["转发历史与重试"])


@lru_cache(maxsize=1)
def get_ledger() -> RelayLedger:
    # Uses the same persistent workdir as UserAutomation in the Dashboard.
    return RelayLedger(DATA / "upstream")


class ReplayInput(BaseModel):
    destination: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("/history")
def relay_history(
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(25, ge=1, le=100),
    status: str | None = Query(None, pattern=r"^(sent|failed|sending)$"),
    account_id: int | None = Query(None, ge=1),
    task_id: int | None = Query(None, ge=1),
):
    return get_ledger().history(
        page=page, page_size=page_size, status=status,
        account_id=account_id, task_id=task_id,
    )


@router.get("/stats")
def relay_stats(
    account_id: int | None = Query(None, ge=1),
    task_id: int | None = Query(None, ge=1),
):
    return get_ledger().stats(account_id=account_id, task_id=task_id)


@router.post("/retry")
async def retry_relay(data: ReplayInput, request: Request):
    """Retry exactly one failed delivery, never a successful/inflight row.

    Existing legacy records without provenance are read-only. The current
    task, route, filters and freshly fetched Telegram message must still
    match the recorded failed delivery; the ledger holds no message payload.
    """
    rate_limit(
        "relay-retry:" + (request.client.host if request.client else "local"),
        limit=20, window=60,
    )
    destination = data.destination.strip().lower()
    ledger = get_ledger()
    record = ledger.get(destination, data.fingerprint)
    if not record:
        raise HTTPException(404, "转发记录不存在")
    if record["status"] != "failed":
        raise HTTPException(409, "只有失败的记录允许重试")
    if not record["retryable"]:
        raise HTTPException(409, "旧记录缺少账号和规则信息，不能安全重试")
    with Session() as db:
        task = db.get(Task, record["task_id"])
        account = db.get(Account, record["account_id"])
        if (
            task is None
            or account is None
            or task.account_id != record["account_id"]
            or task.kind != "automation"
            or not task.enabled
        ):
            raise HTTPException(409, "原账号或已启用的自动化任务不存在")
        task_config = task.config
        task_id = task.id
        account_id = task.account_id

    config = AutomationConfig.model_validate(task_config)
    rule = next(
        (x for x in config.rules if x.id == record["rule_id"] and x.enabled),
        None,
    )
    if rule is None:
        raise HTTPException(409, "原转发规则不存在或已禁用")
    handler_index = record["handler_index"]
    target_index = record["target_index"]
    if handler_index < 0 or handler_index >= len(rule.handlers):
        raise HTTPException(409, "原转发动作已被修改")
    handler = rule.handlers[handler_index]
    if handler.handler != "relay_message":
        raise HTTPException(409, "原转发动作已被修改")
    params = handler.params or {}
    targets = params.get("targets") or []
    if target_index < 0 or target_index >= len(targets):
        raise HTTPException(409, "原转发目标已被修改")
    target = targets[target_index]
    if (
        not isinstance(target, dict)
        or str(target.get("chat_id", "")).strip().lower() != destination
        or target.get("mode", "forward") != record["action"]
        or target.get("dedup", "message") != record["dedup"]
    ):
        raise HTTPException(409, "转发目标设置已改变，请重新检查任务")

    source = record["source_chat"]
    source_id = int(source) if source.lstrip("-").isdecimal() else source
    try:
        async with asyncio.timeout(75):
            worker = await telegram.worker(account_id, UserAutomation, task_id)
            worker.account_id = account_id
            worker.config = config
            message = await worker.app.get_messages(
                source_id, record["source_message"]
            )
            if (
                message is None
                or getattr(message, "empty", False)
                or getattr(message, "chat", None) is None
                or str(message.chat.id) != source
                or message.id != record["source_message"]
            ):
                raise HTTPException(409, "原始消息不可读取或已被删除，不能重试")
            triggers = [
                trigger
                for trigger in rule.triggers
                if trigger.type == "message"
            ]
            if not any(
                worker._match_message_trigger(trigger, message)
                for trigger in triggers
            ):
                raise HTTPException(409, "原始消息已不再符合来源监听条件")
            if rule.filters and not worker._match_filter(rule.filters, message):
                raise HTTPException(409, "原始消息已不再符合当前筛选规则")

            event = Event(
                type="message", chat_id=message.chat.id, message=message,
                now=datetime.now(timezone.utc), trigger_id="manual_replay",
                rule_id=rule.id,
            )
            context = AutomationContext(
                vars={**rule.vars, **worker.state.get_rule_vars(rule.id)},
                state=worker.state, client=worker.app,
                logger=logging.getLogger("tg-signer"), worker=worker,
                workdir=worker.workdir,
            )
            context.handler_index = handler_index
            before = record["attempts"]
            await relay_message(
                event, context, params,
                retry_ref=(
                    destination, data.fingerprint, target_index,
                    record["part_index"], record["payload_digest"],
                ),
            )
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(504, "Telegram 消息获取或转发超时") from exc
    except Exception as exc:
        # Do not disclose Telegram content, API credentials or stack traces.
        raise HTTPException(
            502, f"无法安全重试：{type(exc).__name__}"
        ) from exc
    updated = ledger.get(destination, data.fingerprint)
    if not updated or updated["attempts"] == before:
        raise HTTPException(
            409, "原消息内容或去重标识已改变，未发送任何消息"
        )
    return {"status": updated["status"], "entry": updated}
