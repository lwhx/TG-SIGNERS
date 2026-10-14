"""Authenticated encrypted backup lifecycle; restore is staged, never hot-applied."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .backup_core import (
    BackupError,
    backup_path,
    cancel_queued_restore,
    create_backup,
    import_backup,
    list_backups,
    queue_restore,
    restore_status,
    verify_backup,
)
from .db import DATA
from .security import rate_limit

router = APIRouter(prefix="/maintenance/backups", tags=["P8 加密备份与恢复"])
BACKUP_LOCK = threading.Lock()


class BackupSecret(BaseModel):
    passphrase: str = Field(min_length=16, max_length=1024)


class RestoreInput(BackupSecret):
    confirmation: str


class DeleteInput(BaseModel):
    confirmation: str


def _handle(exc: BackupError) -> HTTPException:
    return HTTPException(400, str(exc))


def _lock_run(function):
    if not BACKUP_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已有备份或恢复验证操作正在进行")
    try:
        return function()
    except BackupError as exc:
        raise _handle(exc) from exc
    finally:
        BACKUP_LOCK.release()


@router.get("")
def backups():
    return {"items": _lock_run(lambda: list_backups(DATA))}


@router.get("/restore-status")
def pending_restore():
    return restore_status(DATA)


@router.post("", status_code=201)
async def backup_create(body: BackupSecret, request: Request):
    rate_limit("create-backup:" + (request.client.host if request.client else "local"), 5, 600)
    return await asyncio.to_thread(_lock_run, lambda: create_backup(DATA, body.passphrase))


@router.post("/import", status_code=201)
async def backup_import(
    request: Request,
    file: UploadFile = File(...),
    passphrase: str = Form(..., min_length=16, max_length=1024),
):
    rate_limit("import-backup:" + (request.client.host if request.client else "local"), 4, 600)
    chunks = []
    length = 0
    try:
        while chunk := await file.read(1048576):
            length += len(chunk)
            if length > 100 * 1024 * 1024:
                raise HTTPException(413, "上传的备份文件超过 100 MiB")
            chunks.append(chunk)
    finally:
        await file.close()
    return await asyncio.to_thread(
        _lock_run, lambda: import_backup(DATA, b"".join(chunks), passphrase)
    )


@router.get("/{backup_id}/download")
def backup_download(backup_id: str):
    try:
        file: Path = backup_path(DATA, backup_id)
    except BackupError as exc:
        raise HTTPException(404, "备份不存在") from exc
    return FileResponse(
        file, media_type="application/octet-stream",
        filename="tg-signer-" + backup_id + ".tgsb",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{backup_id}/verify")
async def backup_verify(backup_id: str, body: BackupSecret, request: Request):
    rate_limit("verify-backup:" + (request.client.host if request.client else "local"), 6, 600)
    return await asyncio.to_thread(
        _lock_run, lambda: verify_backup(DATA, backup_id, body.passphrase)
    )


@router.post("/restore-cancel")
async def backup_restore_cancel(body: DeleteInput):
    if body.confirmation != "CANCEL":
        raise HTTPException(400, "取消待恢复操作需要 CANCEL 确认")
    return await asyncio.to_thread(_lock_run, lambda: cancel_queued_restore(DATA))


@router.post("/{backup_id}/restore")
async def backup_restore(backup_id: str, body: RestoreInput, request: Request):
    if body.confirmation != "RESTORE":
        raise HTTPException(400, "恢复操作需要 RESTORE 确认")
    rate_limit("restore-backup:" + (request.client.host if request.client else "local"), 3, 600)
    return await asyncio.to_thread(
        _lock_run, lambda: queue_restore(DATA, backup_id, body.passphrase)
    )


@router.delete("/{backup_id}")
async def backup_delete(backup_id: str, body: DeleteInput):
    if body.confirmation != "DELETE":
        raise HTTPException(400, "删除备份需要 DELETE 确认")

    def remove():
        path = backup_path(DATA, backup_id)
        status = restore_status(DATA)
        if status["pending"] and status["pending"]["id"] == backup_id:
            raise BackupError("待恢复的备份不能删除")
        path.unlink()
        return {"deleted": backup_id}

    return await asyncio.to_thread(_lock_run, remove)
