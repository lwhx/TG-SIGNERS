"""Validated general Telegram message relay presets and regex match preview."""

from __future__ import annotations

import re
from string import Formatter
from typing import Any, Literal

import regex
from pydantic import BaseModel, Field

MODES = {"forward", "copy", "text", "extract", "template"}
DEDUP = {"message", "content", "off"}
MATCH_MODES = {"all", "contains", "exact", "regex"}


class RelayPreviewInput(BaseModel):
    text_rule: Literal["all", "contains", "exact", "regex"] = "regex"
    pattern: str = Field(default="", max_length=2048)
    sample: str = Field(default="", max_length=8000)
    ignore_case: bool = True


def preview_match(data: RelayPreviewInput) -> dict[str, Any]:
    """Match user-supplied text with bounded execution time."""
    if data.text_rule == "all":
        return {"matched": True, "match": "(全部消息)", "groups": [], "named_groups": {}}
    if data.text_rule == "contains":
        haystack = data.sample.casefold() if data.ignore_case else data.sample
        needle = data.pattern.casefold() if data.ignore_case else data.pattern
        matched = bool(needle) and needle in haystack
        return {
            "matched": matched, "match": data.pattern if matched else "",
            "groups": [], "named_groups": {},
        }
    if data.text_rule == "exact":
        left = data.sample.casefold() if data.ignore_case else data.sample
        right = data.pattern.casefold() if data.ignore_case else data.pattern
        matched = left == right
        return {
            "matched": matched, "match": data.sample if matched else "",
            "groups": [], "named_groups": {},
        }
    try:
        flags = regex.IGNORECASE if data.ignore_case else 0
        match = regex.search(data.pattern, data.sample, flags=flags, timeout=0.2)
    except (regex.error, TimeoutError) as exc:
        raise ValueError(f"正则测试失败：{type(exc).__name__}: {str(exc)[:200]}") from exc
    if match is None:
        return {"matched": False, "match": "", "groups": [], "named_groups": {}}
    return {
        "matched": True,
        "match": match.group(0)[:2000],
        "span": list(match.span()),
        "groups": [(v or "")[:2000] for v in match.groups()[:20]],
        "named_groups": {k: (v or "")[:2000] for k, v in match.groupdict().items()},
    }


def validate_relay_config(config: dict[str, Any]) -> None:
    """Validate relay handlers before committing dashboard task configuration."""
    for rule in config.get("rules", []):
        for action in rule.get("handlers", []):
            if action.get("handler") != "relay_message":
                continue
            params = action.get("params", {})
            targets = params.get("targets")
            if not isinstance(targets, list) or not 1 <= len(targets) <= 25:
                raise ValueError("通用消息转发必须有 1～25 个目标")
            sources: set[str] = set()
            for trigger in rule.get("triggers", []):
                if trigger.get("type") == "message":
                    data = trigger.get("params", {})
                    for item in (data.get("chat_ids") or []) + (
                        [data["chat_id"]] if data.get("chat_id") is not None else []
                    ):
                        sources.add(str(item).strip().lower())
            for index, target in enumerate(targets, 1):
                if not isinstance(target, dict):
                    raise ValueError(f"第 {index} 个转发目标格式错误")
                dest = target.get("chat_id")
                if not isinstance(dest, (str, int)) or not str(dest).strip():
                    raise ValueError(f"第 {index} 个转发目标缺少 Chat ID")
                if str(dest).strip().lower() in sources:
                    raise ValueError(f"第 {index} 个转发目标与来源相同")
                mode = target.get("mode", "forward")
                if mode not in MODES:
                    raise ValueError(f"第 {index} 个转发目标模式无效")
                if target.get("dedup", "message") not in DEDUP:
                    raise ValueError(f"第 {index} 个转发目标去重策略无效")
                pattern = target.get("pattern", "")
                if mode == "extract" and not pattern:
                    raise ValueError(f"第 {index} 个目标必须设置提取正则")
                if pattern:
                    if not isinstance(pattern, str) or len(pattern) > 2048:
                        raise ValueError(f"第 {index} 个提取正则超长或无效")
                    try:
                        compiled = re.compile(pattern)
                    except re.error as exc:
                        raise ValueError(f"第 {index} 个正则错误：{exc}") from exc
                    if mode == "extract":
                        group = target.get("group", 1)
                        if isinstance(group, int) and 0 <= group <= compiled.groups:
                            pass
                        elif isinstance(group, str) and group in compiled.groupindex:
                            pass
                        else:
                            raise ValueError(f"第 {index} 个正则捕获组不存在")
                if mode == "template":
                    template = target.get("template")
                    if not isinstance(template, str) or not template:
                        raise ValueError(f"第 {index} 个目标消息模板不能为空")
                    try:
                        list(Formatter().parse(template))
                    except ValueError as exc:
                        raise ValueError(f"第 {index} 个模板格式错误") from exc
