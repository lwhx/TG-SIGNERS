"""P6 task organization, safe duplication, bulk operations and revisions.

All mutations retain existing Telegram Session data and the P4 relay ledger.
Snapshots store task configuration only, never API credentials or message text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from .db import Account, Session, Task, TaskVersion
from .engine import task_engine
from .schemas import TaskInput

router = APIRouter(prefix="/tasks", tags=["P6 任务管理与配置版本"])
mutation_lock = asyncio.Lock()

TASK_FIELDS = (
    "name", "account_id", "kind", "enabled", "cron", "timezone",
    "delay_min", "delay_max", "success_pattern", "failure_pattern",
    "response_timeout", "folder", "group_name", "config",
)


def task_snapshot(task: Task) -> dict:
    return {key: deepcopy(getattr(task, key)) for key in TASK_FIELDS}


def task_digest(snapshot: dict) -> str:
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def revision_number(db, task_id: int) -> int:
    return db.scalar(
        select(func.max(TaskVersion.version)).where(TaskVersion.task_id == task_id)
    ) or 0


def add_revision(db, task: Task, operation: str) -> TaskVersion:
    snapshot = task_snapshot(task)
    entry = TaskVersion(
        task_id=task.id,
        version=revision_number(db, task.id) + 1,
        operation=operation[:50],
        snapshot=snapshot,
        digest=task_digest(snapshot),
    )
    db.add(entry)
    db.flush()
    return entry


def ensure_baseline(db, task: Task) -> None:
    if revision_number(db, task.id) == 0:
        add_revision(db, task, "baseline")


def initialize_task_versions() -> int:
    """Make one baseline for every upgraded task, without touching its config."""
    count = 0
    with Session() as db:
        for task in db.scalars(select(Task).order_by(Task.id)):
            if revision_number(db, task.id) == 0:
                add_revision(db, task, "baseline")
                count += 1
        db.commit()
    return count


def get_task(db, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


class DuplicateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    account_id: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str | None):
        if value is not None and not value.strip():
            raise ValueError("任务名称不能为空")
        return value.strip() if value else None


class BatchRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=50)
    operation: Literal["enable", "disable", "move_group", "delete"]
    group_name: str = Field(default="", max_length=60)
    dry_run: bool = False
    confirmation: str | None = None

    @field_validator("ids")
    @classmethod
    def valid_ids(cls, ids: list[int]):
        if any(type(value) is not int or value <= 0 for value in ids):
            raise ValueError("任务 ID 必须是正整数")
        if len(set(ids)) != len(ids):
            raise ValueError("任务 ID 不能重复")
        return ids

    @field_validator("group_name")
    @classmethod
    def normalize_group(cls, value):
        return _normalize_group(value)


def _normalize_group(value: str) -> str:
    import re

    value = value.strip()
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("分组名称不能包含控制字符")
    return value


class RestoreRequest(BaseModel):
    expected_version: int = Field(ge=1)


@router.post("/{task_id}/duplicate", status_code=201)
async def duplicate_task(task_id: int, data: DuplicateRequest):
    """Duplicate all configuration, intentionally disabled to avoid surprise sends."""
    async with mutation_lock:
        with Session() as db:
            source = get_task(db, task_id)
            attrs = task_snapshot(source)
            attrs["enabled"] = False
            attrs["name"] = (data.name or ("副本 · " + source.name)[:100])
            if data.account_id is not None:
                attrs["account_id"] = data.account_id
            if not db.get(Account, attrs["account_id"]):
                raise HTTPException(404, "目标账号不存在")
            validated = TaskInput.model_validate(attrs)
            duplicate = Task(**validated.model_dump())
            db.add(duplicate)
            db.flush()
            add_revision(db, duplicate, f"copied-from:{task_id}")
            db.commit()
            created_id = duplicate.id
    return {"id": created_id, "enabled": False, "message": "任务已复制，默认停用"}


@router.post("/batch")
async def batch_tasks(data: BatchRequest):
    """Preflight every selected ID. Deletes require an explicit confirmation token."""
    async with mutation_lock:
        with Session() as db:
            tasks = list(db.scalars(select(Task).where(Task.id.in_(data.ids))))
            if len(tasks) != len(data.ids):
                raise HTTPException(404, "部分任务已被删除；未执行批量操作")
            previews = [{"id": t.id, "name": t.name, "kind": t.kind,
                         "enabled": t.enabled, "group_name": t.group_name} for t in tasks]
        if data.dry_run:
            return {"dry_run": True, "count": len(tasks), "items": previews}
        if data.operation == "delete" and data.confirmation != "DELETE":
            raise HTTPException(400, "批量删除必须明确提交 DELETE 确认")
        if data.operation == "move_group":
            group = _normalize_group(data.group_name)
        else:
            group = ""
        restart_ids = data.ids if data.operation in {"enable", "disable", "delete"} else []
        for task_id in restart_ids:
            await task_engine.stop(task_id)
        changed: list[int] = []
        try:
            with Session() as db:
                current = list(db.scalars(select(Task).where(Task.id.in_(data.ids))))
                if len(current) != len(data.ids):
                    raise HTTPException(409, "任务列表已变化，请重新确认")
                for item in current:
                    if data.operation == "delete":
                        db.delete(item)
                        changed.append(item.id)
                        continue
                    desired = (
                        True if data.operation == "enable" else
                        False if data.operation == "disable" else item.enabled
                    )
                    next_group = group if data.operation == "move_group" else item.group_name
                    if item.enabled == desired and item.group_name == next_group:
                        continue
                    ensure_baseline(db, item)
                    item.enabled = desired
                    item.group_name = next_group
                    if data.operation in {"enable", "disable"}:
                        item.next_run = None
                    add_revision(db, item, "batch-" + data.operation)
                    changed.append(item.id)
                db.commit()
        finally:
            for task_id in restart_ids:
                await task_engine.reconcile(task_id)
        return {"dry_run": False, "operation": data.operation,
                "count": len(changed), "ids": changed}


@router.get("/{task_id}/versions")
def list_versions(task_id: int):
    with Session() as db:
        task = get_task(db, task_id)
        ensure_baseline(db, task)
        db.commit()
        entries = list(db.scalars(
            select(TaskVersion).where(TaskVersion.task_id == task_id)
            .order_by(TaskVersion.version.desc()).limit(100)
        ))
        return {
            "task_id": task_id,
            "current_version": revision_number(db, task_id),
            "items": [{
                "version": v.version, "operation": v.operation,
                "created_at": v.created_at, "digest": v.digest,
                "name": v.snapshot["name"],
                "group_name": v.snapshot.get("group_name", ""),
                "enabled": v.snapshot["enabled"],
            } for v in entries],
            "current": task_snapshot(task),
        }


@router.get("/{task_id}/versions/{version}")
def get_version(task_id: int, version: int):
    with Session() as db:
        get_task(db, task_id)
        item = db.scalar(select(TaskVersion).where(
            TaskVersion.task_id == task_id, TaskVersion.version == version
        ))
        if not item:
            raise HTTPException(404, "配置版本不存在")
        return {"version": item.version, "operation": item.operation,
                "created_at": item.created_at, "digest": item.digest,
                "snapshot": item.snapshot}


@router.post("/{task_id}/versions/{version}/restore")
async def restore_version(task_id: int, version: int, data: RestoreRequest):
    """Restore a validated snapshot without re-enabling a disabled task."""
    async with mutation_lock:
        with Session() as db:
            current = get_task(db, task_id)
            if revision_number(db, task_id) != data.expected_version:
                raise HTTPException(409, "当前版本已发生变化，请刷新后重试")
            saved = db.scalar(select(TaskVersion).where(
                TaskVersion.task_id == task_id, TaskVersion.version == version
            ))
            if not saved:
                raise HTTPException(404, "配置版本不存在")
            attrs = deepcopy(saved.snapshot)
            attrs["enabled"] = current.enabled
            if not db.get(Account, attrs["account_id"]):
                raise HTTPException(409, "历史版本关联的账号已不存在")
            validated = TaskInput.model_validate(attrs).model_dump()
        await task_engine.stop(task_id)
        try:
            with Session() as db:
                current = get_task(db, task_id)
                if revision_number(db, task_id) != data.expected_version:
                    raise HTTPException(409, "配置已变化，恢复操作已取消")
                for key, value in validated.items():
                    setattr(current, key, value)
                current.next_run = None
                revision = add_revision(db, current, f"restored:{version}")
                db.commit()
                result = {"task_id": task_id, "version": revision.version,
                          "restored_from": version, "enabled": current.enabled}
        finally:
            await task_engine.reconcile(task_id)
        return result
