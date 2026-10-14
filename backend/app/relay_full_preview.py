"""P6 dry-run preview of complete relay targets; never sends or stores messages.

Uses the same target-content builder as the real relay handler. Matching only
supports message-trigger rules; runtime state, other handler side effects and
Telegram permissions cannot be predicted offline.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from tg_signer.automation.message_relay import _content_for_target
from tg_signer.config import AutomationConfig

from .relay import RelayPreviewInput, preview_match, validate_relay_config
from .security import rate_limit

router = APIRouter(prefix="/relay", tags=["P6 完整转发预览"])
MAX_RULES = 20
MAX_RESULTS = 100


class FullPreviewInput(BaseModel):
    config: dict[str, Any]
    sample: str = Field(max_length=8000)
    source_chat: str = Field(min_length=1, max_length=255)
    source_message: int = Field(default=1, gt=0)
    sender_id: str = Field(default="", max_length=255)
    is_self: bool = False
    reply_to_me: bool = False
    reply_message_id: int | None = Field(default=None, gt=0)
    has_media: bool = False

    @field_validator("source_chat")
    @classmethod
    def nonempty_source(cls, source):
        if not source.strip():
            raise ValueError("请输入样例来源 Chat ID 或 @username")
        return source.strip()


def _same_chat(sample: str, configured: Any) -> bool:
    return str(sample).strip().lower().lstrip("@") == str(configured).strip().lower().lstrip("@")


def _chat_matches(source: str, item: dict) -> bool:
    options = list(item.get("chat_ids") or [])
    if item.get("chat_id") is not None:
        options.append(item["chat_id"])
    return not options or any(_same_chat(source, value) for value in options)


def _sender_matches(data: FullPreviewInput, item: dict) -> bool:
    values = item.get("from_user_ids") or []
    if not values:
        return True
    sender = data.sender_id.strip().lower().lstrip("@")
    return any(
        (str(value).strip().lower().lstrip("@") in {"me", "self"} and data.is_self)
        or (sender and sender == str(value).strip().lower().lstrip("@"))
        for value in values
    )


def _matching_triggers(data: FullPreviewInput, rule: dict) -> list[int]:
    matched = []
    for i, trigger in enumerate(rule.get("triggers", [])):
        if trigger.get("type") != "message":
            continue
        cfg = trigger.get("params") or {}
        if not _chat_matches(data.source_chat, cfg) or not _sender_matches(data, cfg):
            continue
        if cfg.get("reply_to_me") and not data.reply_to_me:
            continue
        if cfg.get("reply_to_message_id") and cfg["reply_to_message_id"] != data.reply_message_id:
            continue
        matched.append(i)
    return matched


def _filters_match(data: FullPreviewInput, rule: dict) -> bool:
    cfg = rule.get("filters") or {}
    if not _chat_matches(data.source_chat, cfg) or not _sender_matches(data, cfg):
        return False
    result = preview_match(RelayPreviewInput(
        sample=data.sample,
        text_rule=cfg.get("text_rule") or "all",
        pattern=cfg.get("text_value") or "",
        ignore_case=cfg.get("ignore_case", True),
    ))
    return result["matched"]


def dry_run(data: FullPreviewInput) -> dict:
    config = AutomationConfig.model_validate(data.config).model_dump(mode="json")
    if len(config.get("rules", [])) > MAX_RULES:
        raise HTTPException(422, f"每次最多预览 {MAX_RULES} 条规则")
    validate_relay_config(config)
    result: list[dict] = []
    deliveries = 0
    truncated = False
    for rule in config.get("rules", []):
        relay_handlers = [
            (i, h) for i, h in enumerate(rule.get("handlers", []))
            if h.get("handler") == "relay_message"
        ]
        if not relay_handlers:
            continue
        enabled = rule.get("enabled", True)
        triggers = _matching_triggers(data, rule) if enabled else []
        filtered = _filters_match(data, rule) if triggers else False
        reason = (
            "规则已停用" if not enabled else
            "样例未命中来源或发送者触发器" if not triggers else
            "样例未通过文本或来源筛选" if not filtered else ""
        )
        rule_result = {"rule_id": rule["id"], "matched": bool(triggers and filtered),
                       "reason": reason, "trigger_indices": triggers, "targets": []}
        if not rule_result["matched"]:
            result.append(rule_result)
            continue

        message = SimpleNamespace(
            id=data.source_message, text=data.sample, caption=None,
            media="sample-media" if data.has_media else None,
            from_user=SimpleNamespace(id=data.sender_id, is_self=data.is_self),
            outgoing=data.is_self,
        )
        event = SimpleNamespace(chat_id=data.source_chat, rule_id=rule["id"])
        context = SimpleNamespace(vars=rule.get("vars") or {})
        for index, handler in relay_handlers:
            params = handler.get("params") or {}
            prefix = any(
                entry.get("handler") != "relay_message"
                for entry in rule.get("handlers", [])[:index]
            )
            for target in params.get("targets") or []:
                if deliveries >= MAX_RESULTS:
                    truncated = True
                    break
                mode = target.get("mode", "forward")
                chat_id = str(target.get("chat_id", "")).strip()
                row = {
                    "handler_index": index, "destination": chat_id,
                    "mode": mode, "dedup": target.get("dedup", "message"),
                    "would_send": False, "count": 0, "payloads": [],
                    "conditional": prefix,
                    "reason": "", "note": "",
                }
                if params.get("ignore_self", True) and data.is_self:
                    row["reason"] = "已忽略自己发出的消息"
                elif _same_chat(data.source_chat, chat_id):
                    row["reason"] = "来源和目标相同，禁止发送"
                elif mode in {"forward", "copy"}:
                    row["would_send"] = True
                    row["count"] = 1
                    row["note"] = (
                        "将原样转发 Telegram 消息（不生成文本），包含媒体" if mode == "forward"
                        else "将复制原消息（不显示来源），包含媒体时复制媒体"
                    )
                else:
                    try:
                        texts = _content_for_target(dict(target, mode=mode), message, event, context)
                        row["count"] = len([text for text in texts if text])
                        row["would_send"] = row["count"] > 0
                        row["payloads"] = [str(text)[:2000] for text in texts[:5] if text]
                        if row["count"] > 5:
                            row["note"] = "仅展示前 5 条，实际提取上限为 20 条"
                        if not row["would_send"]:
                            row["reason"] = "没有可发送的文本或正则未匹配"
                    except (ValueError, KeyError, TypeError, IndexError, TimeoutError) as exc:
                        row["reason"] = "目标预览失败：" + type(exc).__name__
                if row["would_send"]:
                    deliveries += row["count"] * len(triggers)
                rule_result["targets"].append(row)
            if truncated:
                break
        result.append(rule_result)
        if truncated:
            break
    return {
        "rules": result,
        "potential_messages": deliveries,
        "truncated": truncated,
        "warning": (
            "仅静态预览：不发送 Telegram 消息、不访问账号或读写去重账本。"
            "前置 Handler、状态变量、Telegram 权限、实际去重命中和 Bot 处理结果"
            "无法离线确定；多个匹配触发器会分别执行转发。"
        ),
    }


@router.post("/full-preview", summary="按真实转发内容构造器模拟所有目标，绝不发送消息")
def full_relay_preview(data: FullPreviewInput, request: Request):
    rate_limit(
        "relay-full-preview:" + (request.client.host if request.client else "local"),
        limit=20, window=60,
    )
    try:
        return dry_run(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)[:300]) from exc
