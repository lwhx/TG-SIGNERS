import asyncio
import dataclasses
import json
import logging
import os
import secrets
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import yaml
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from pyrogram import Client, errors
from pyrogram.enums import ChatMembersFilter
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import defer
from tg_signer import __version__ as upstream_version
from tg_signer.automation.handlers import list_handlers, register_builtin_handlers
from tg_signer.config import (
    AutomationConfig,
    MonitorConfig,
    SignConfigV3,
    parse_chat_id_or_username,
)
from tg_signer.core import (
    _explicit_folder_chats,
    _select_chat_folder,
    get_api_config,
    get_proxy,
)
from tg_signer.sign_record_store import SignRecordStore

from .auth import auth, disconnect, persist_account, telegram_error
from .backup_api import router as backup_router
from .db import (
    DATA,
    Account,
    Run,
    Session,
    Task,
    get_setting,
    set_setting,
)
from .engine import task_engine
from .events import LogBridge, bus
from .maintenance import check_relay_alerts, check_relay_recovery, scheduled_cleanup
from .maintenance import router as maintenance_router
from .notifications import notify
from .panel_api import private_router as panel_private_router
from .panel_api import public_router as panel_public_router
from .performance import router as performance_router
from .relay import RelayPreviewInput, preview_match
from .relay_backfill import recover_backfills, stop_all_backfills
from .relay_backfill import router as relay_backfill_router
from .relay_full_preview import router as relay_full_preview_router
from .relay_history import router as relay_history_router
from .relay_operations import router as relay_operations_router
from .relay_p7 import router as relay_p7_router
from .relay_retry import process_retries
from .relay_retry import router as relay_retry_router
from .schema_migrations import migrate_schema
from .schemas import (
    AccountUpdate,
    Check2FA,
    SendCode,
    SignIn,
    TaskInput,
    ToolInput,
    validate_proxy,
)
from .security import (
    COOKIE,
    decrypt,
    encrypt,
    passwords,
    persistent_secret,
    rate_limit,
    require_admin,
    valid_token,
)
from .task_admin import (
    add_revision,
    ensure_baseline,
    initialize_task_versions,
    mutation_lock,
    task_snapshot,
)
from .task_admin import router as task_admin_router
from .telegram import telegram
from .version import DASHBOARD_VERSION


@asynccontextmanager
async def lifespan(app):
    os.umask(0o077)
    migrate_schema()
    initialize_task_versions()
    recover_backfills()
    if not get_setting("password_hash"):
        password = os.getenv("ADMIN_PASSWORD") or persistent_secret(
            "initial-password.txt", lambda: secrets.token_urlsafe(20)
        )
        if len(password) < 12:
            raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters")
        set_setting("password_hash", passwords.hash(password))
    bridge = LogBridge()
    logging.getLogger("tg-signer").addHandler(bridge)
    logging.getLogger("tg-signer").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from .relay_history import get_ledger
    await asyncio.to_thread(lambda: get_ledger().recover_stale(all_sending=True))
    await task_engine.start()
    task_engine.scheduler.add_job(auth.cleanup, "interval", seconds=30, id="auth-cleanup")
    task_engine.scheduler.add_job(
        check_sessions, "interval", minutes=5, id="session-check", max_instances=1, coalesce=True
    )
    task_engine.scheduler.add_job(
        scheduled_cleanup, "interval", hours=1, id="history-cleanup", max_instances=1, coalesce=True
    )
    task_engine.scheduler.add_job(
        check_relay_alerts, "interval", minutes=1, id="relay-alerts", max_instances=1, coalesce=True
    )
    task_engine.scheduler.add_job(
        process_retries, "interval", seconds=30, id="relay-retries", max_instances=1, coalesce=True
    )
    task_engine.scheduler.add_job(
        check_relay_recovery, "interval", minutes=1, id="relay-recovery", max_instances=1, coalesce=True
    )
    bus.emit(f"控制台已启动 · tg-signer {upstream_version}")
    yield
    await stop_all_backfills()
    await task_engine.shutdown()
    for key in list(auth.flows):
        await auth.cancel(key)
    logging.getLogger("tg-signer").removeHandler(bridge)


app = FastAPI(
    title="tg-signer Dashboard API",
    version=DASHBOARD_VERSION,
    description="Telegram 账号、签到、自动化与监控管理。除面板登录、健康检查外，所有接口需要 HttpOnly JWT Cookie 或 Bearer JWT。",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
api = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
app.include_router(panel_public_router)


@app.middleware("http")
async def security_headers(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if (
            origin and urlparse(origin).netloc != request.headers.get("host")
        ) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
        length = request.headers.get("content-length", "0")
        max_body = (
            101 * 1024 * 1024
            if request.url.path == "/api/maintenance/backups/import"
            else 10 * 1024 * 1024
        )
        if not length.isdigit() or int(length) > max_body:
            return JSONResponse({"detail": "请求体超过 10 MB"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValidationError)
@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        {"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]},
        status_code=422,
    )


@app.exception_handler(errors.RPCError)
async def rpc_error(request, exc):
    error = telegram_error(exc)
    return JSONResponse(
        {"detail": error.detail}, status_code=error.status_code, headers=error.headers
    )


@app.exception_handler(TimeoutError)
async def timeout_error(request, exc):
    return JSONResponse({"detail": "连接超时，请检查 Telegram 网络或代理设置"}, status_code=504)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": "参数或配置无效：" + str(exc)[:500]}, status_code=400)


@api.post(
    "/auth/send-code",
    tags=["Telegram 登录"],
    summary="发送/重发验证码，创建十分钟有效的登录状态机",
)
async def send_code(data: SendCode):
    return await auth.send(data)


@api.post(
    "/auth/sign-in",
    tags=["Telegram 登录"],
    summary="校验验证码；返回 AUTHORIZED 或 2FA_REQUIRED",
)
async def sign_in(data: SignIn):
    result = await auth.sign_in(data)
    if result["status"] == "AUTHORIZED":
        await reconnect_account(result["account_id"], connect=False)
    return result


@api.post(
    "/auth/check-2fa",
    tags=["Telegram 登录"],
    summary="校验二步验证密码并加密保存 Session",
)
async def check_2fa(data: Check2FA):
    result = await auth.check_2fa(data)
    await reconnect_account(result["account_id"], connect=False)
    return result


@api.delete(
    "/auth/flows/{flow_id}",
    tags=["Telegram 登录"],
    summary="取消登录并释放 Telegram 连接",
)
async def cancel_auth(flow_id: str):
    await auth.cancel(flow_id)
    return {"status": "cancelled"}


def account_dict(a):
    return {
        "id": a.id,
        "name": a.name,
        "phone": a.phone,
        "username": a.username,
        "user_id": a.user_id,
        "avatar": a.avatar,
        "status": a.status,
        "proxy": proxy_label(decrypt(a.proxy)),
        "last_checked": a.last_checked,
        "created_at": a.created_at,
    }


def proxy_label(value):
    if not value:
        return ""
    u = urlparse(value)
    return f"{u.scheme}://{u.hostname}:{u.port}"


def get_account(db, account_id):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "账号不存在")
    return account


@api.get("/accounts", tags=["账号"], summary="获取账号及最近验证的连接状态")
def accounts():
    with Session() as db:
        return [account_dict(a) for a in db.scalars(select(Account).order_by(Account.id.desc()))]


@api.post(
    "/accounts/import",
    tags=["账号"],
    summary="导入 Pyrogram/Kurigram Session String 或 .session SQLite 文件",
)
async def import_account(
    name: str = Form(""),
    session_string: str = Form(""),
    api_id: int | None = Form(None),
    api_hash: str | None = Form(None),
    proxy: str = Form(""),
    file: UploadFile | None = File(None),
):
    rate_limit("session-import", 10, 300)
    validate_proxy(proxy)
    default_id, default_hash = get_api_config()
    if bool(api_id) != bool(api_hash):
        raise HTTPException(422, "API_ID 与 API_HASH 必须同时填写")
    spec = SendCode(
        phone="+10000000000",
        name=name,
        api_id=api_id or default_id,
        api_hash=api_hash or default_hash,
        proxy=proxy,
    )
    with tempfile.TemporaryDirectory(prefix="tg-import-") as directory:
        if file:
            content = await file.read(5 * 1024 * 1024 + 1)
            if len(content) > 5 * 1024 * 1024 or not content.startswith(b"SQLite format 3\x00"):
                raise HTTPException(400, "需要小于 5 MB 的 Pyrogram/Kurigram SQLite Session 文件")
            path = Path(directory) / "import.session"
            path.write_bytes(content)
            try:
                with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                    columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
                    if not {"auth_key", "user_id", "api_id"} <= columns:
                        raise HTTPException(
                            400,
                            "Session 格式不兼容：需要 Pyrogram/Kurigram 格式，Telethon Session 请先转换",
                        )
            except sqlite3.DatabaseError as exc:
                raise HTTPException(400, "Session 数据库损坏") from exc
            client = Client(
                "import",
                workdir=directory,
                api_id=spec.api_id,
                api_hash=spec.api_hash,
                proxy=get_proxy(proxy or decrypt(get_setting("global_proxy"))),
                no_updates=True,
            )
        else:
            if not 100 <= len(session_string.strip()) <= 1000:
                raise HTTPException(400, "请输入有效的 Pyrogram/Kurigram Session String")
            client = Client(
                "import",
                api_id=spec.api_id,
                api_hash=spec.api_hash,
                session_string=session_string.strip(),
                in_memory=True,
                proxy=get_proxy(proxy or decrypt(get_setting("global_proxy"))),
                no_updates=True,
            )
        try:
            async with asyncio.timeout(60):
                if not await client.connect():
                    raise HTTPException(400, "Session 未授权，请使用手机号登录")
                account_id = await persist_account(client, spec)
        finally:
            await disconnect(client)
    await reconnect_account(account_id, connect=False)
    return {"status": "AUTHORIZED", "account_id": account_id}


@api.post("/accounts/sync", tags=["账号"], summary="验证全部账号会话")
async def sync_accounts():
    with Session() as db:
        ids = list(db.scalars(select(Account.id)))
    results = []
    for account_id in ids:
        try:
            results.append({"id": account_id, **await telegram.ping(account_id)})
        except Exception as exc:
            results.append({"id": account_id, "status": "error", "error": type(exc).__name__})
    return results


async def check_sessions():
    with Session() as db:
        ids = list(
            db.scalars(select(Account.id).where(Account.status.notin_(["invalid", "banned"])))
        )
    semaphore = asyncio.Semaphore(2)

    async def check(account_id):
        async with semaphore:
            try:
                await telegram.ping(account_id)
            except Exception as exc:
                bus.emit(f"会话检查失败：{type(exc).__name__}", "WARNING", account_id=account_id)

    await asyncio.gather(*(check(i) for i in ids))


@api.post("/accounts/{account_id}/ping", tags=["账号"])
async def ping(account_id: int):
    return await telegram.ping(account_id)


async def reconnect_account(account_id, connect=True):
    with Session() as db:
        ids = list(db.scalars(select(Task.id).where(Task.account_id == account_id)))
    for task_id in ids:
        await task_engine.stop(task_id)
    await telegram.close(account_id)
    result = await telegram.ping(account_id) if connect else {"status": "ok"}
    for task_id in ids:
        await task_engine.reconcile(task_id)
    return result


@api.post(
    "/accounts/{account_id}/refresh",
    tags=["账号"],
    summary="重新连接并更新 Session；已失效的会话需重新登录",
)
async def refresh_account(account_id: int):
    return await reconnect_account(account_id)


@api.get(
    "/accounts/{account_id}/export",
    tags=["账号"],
    summary="下载解密后的 Session String",
)
def export_account(account_id: int):
    with Session() as db:
        a = get_account(db, account_id)
        return Response(
            decrypt(a.session),
            media_type="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="account-{account_id}.session_string"',
                "Cache-Control": "no-store",
            },
        )


@api.put("/accounts/{account_id}", tags=["账号"])
async def update_account(account_id: int, data: AccountUpdate):
    with Session() as db:
        a = get_account(db, account_id)
        a.name = data.name
        if data.proxy is not None:
            a.proxy = encrypt(data.proxy)
        db.commit()
    if data.proxy is not None:
        await reconnect_account(account_id, connect=False)
    return {"status": "ok"}


@api.delete("/accounts/{account_id}", tags=["账号"], summary="删除本地账号；绑定任务必须先删除")
async def delete_account(account_id: int):
    with Session() as db:
        a = get_account(db, account_id)
        if db.scalar(select(Task.id).where(Task.account_id == account_id).limit(1)):
            raise HTTPException(409, "请先删除绑定到此账号的任务")
        await telegram.close(account_id)
        db.delete(a)
        db.commit()
    return {"status": "deleted"}


def task_dict(task):
    return {c.name: getattr(task, c.name) for c in Task.__table__.columns} | {
        "running": task.id in task_engine.running
    }


@api.get("/tasks", tags=["任务"])
def tasks():
    with Session() as db:
        return [task_dict(t) for t in db.scalars(select(Task).order_by(Task.id.desc()))]


@api.post("/tasks", tags=["任务"], status_code=201, summary="创建签到 / 自动化 / 监控任务")
async def create_task(data: TaskInput):
    async with mutation_lock:
        with Session() as db:
            get_account(db, data.account_id)
            task = Task(**data.model_dump())
            db.add(task)
            db.flush()
            add_revision(db, task, "created")
            db.commit()
            task_id = task.id
        await task_engine.reconcile(task_id)
        with Session() as db:
            return task_dict(db.get(Task, task_id))


@api.post(
    "/tasks/validate",
    tags=["任务"],
    summary="使用上游 Pydantic 模型校验完整配置，支持 JSON / YAML",
)
def validate_task(data: TaskInput):
    return {"valid": True, "config": data.config}


@api.post(
    "/relay/preview",
    tags=["自动化"],
    summary="限时预览消息文本匹配与正则捕获组",
)
def preview_relay(data: RelayPreviewInput):
    try:
        return preview_match(data)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@api.post("/tasks/run-all", tags=["任务"], summary="执行全部启用的签到任务")
async def run_all():
    with Session() as db:
        ids = list(db.scalars(select(Task.id).where(Task.enabled.is_(True), Task.kind == "signer")))
    return [task_engine.launch(task_id) for task_id in ids if task_id not in task_engine.running]


@api.get("/tasks/{task_id}", tags=["任务"])
def task_detail(task_id: int):
    with Session() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return task_dict(task)


@api.put("/tasks/{task_id}", tags=["任务"])
async def update_task(task_id: int, data: TaskInput):
    async with mutation_lock:
        with Session() as db:
            task = db.get(Task, task_id)
            if not task:
                raise HTTPException(404, "任务不存在")
            get_account(db, data.account_id)
            changed = task_snapshot(task) != data.model_dump()
        if not changed:
            return task_detail(task_id)
        await task_engine.stop(task_id)
        try:
            with Session() as db:
                task = db.get(Task, task_id)
                ensure_baseline(db, task)
                for key, value in data.model_dump().items():
                    setattr(task, key, value)
                task.next_run = None
                add_revision(db, task, "updated")
                db.commit()
        finally:
            await task_engine.reconcile(task_id)
        return task_detail(task_id)


@api.delete("/tasks/{task_id}", tags=["任务"])
async def delete_task(task_id: int):
    async with mutation_lock:
        with Session() as db:
            if not db.get(Task, task_id):
                raise HTTPException(404, "任务不存在")
        await task_engine.stop(task_id)
        try:
            with Session() as db:
                task = db.get(Task, task_id)
                if not task:
                    raise HTTPException(404, "任务不存在")
                db.delete(task)
                db.commit()
        finally:
            await task_engine.reconcile(task_id)
        return {"status": "deleted"}


@api.post(
    "/tasks/{task_id}/run",
    tags=["任务"],
    status_code=202,
    summary="立即执行，跳过随机延迟；禁止重复运行",
)
async def run_task(task_id: int):
    return task_engine.launch(task_id)


@api.post(
    "/tasks/{task_id}/stop",
    tags=["任务"],
    summary="停止当前执行（持久监听任务同时禁用）",
)
async def stop_task(task_id: int):
    with Session() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task.kind != "signer":
            task.enabled = False
            db.commit()
    await task_engine.stop(task_id)
    return {"status": "stopped"}


@api.get(
    "/tasks/{task_id}/export",
    tags=["任务"],
    summary="导出可直接用于 tg-signer CLI 的配置",
)
def export_task(task_id: int, format: str = "json"):
    task = task_detail(task_id)
    value = task["config"]
    if task["kind"] == "signer":
        value["sign_at"], value["random_seconds"] = task["cron"], task["delay_max"]
    body = (
        yaml.safe_dump(value, allow_unicode=True)
        if format == "yaml"
        else json.dumps(value, ensure_ascii=False, indent=2)
    )
    return Response(
        body,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="task-{task_id}.{format if format == "yaml" else "json"}"'
        },
    )


@api.post(
    "/tasks/{task_id}/multi-run",
    tags=["任务"],
    summary="将同一配置复制并执行到所选账号",
)
async def multi_run(task_id: int, account_ids: list[int]):
    source = task_detail(task_id)
    if source["kind"] != "signer":
        raise HTTPException(400, "批量执行只适用于签到任务")
    ids = list(dict.fromkeys(account_ids))
    if not ids or len(ids) > 50:
        raise HTTPException(400, "请选择 1～50 个账号")
    with Session() as db:
        for account_id in ids:
            get_account(db, account_id)
        targets = []
        for account_id in ids:
            if account_id == source["account_id"]:
                targets.append(task_id)
                continue
            fields = TaskInput.model_validate(
                {
                    **source,
                    "account_id": account_id,
                    "name": source["name"] + " · 副本",
                    "enabled": False,
                }
            ).model_dump()
            task = Task(**fields)
            db.add(task)
            db.flush()
            add_revision(db, task, "multi-run-copy")
            targets.append(task.id)
        db.commit()
    return [task_engine.launch(i) for i in targets if i not in task_engine.running]


def run_dict(run):
    return {c.name: getattr(run, c.name) for c in Run.__table__.columns}


@api.get("/logs", tags=["日志"], summary="分页查询执行记录和 Telegram 原始回复")
def logs(
    account_id: int | None = None,
    task_id: int | None = None,
    status: str = "",
    q: str = "",
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=100),
):
    query = select(Run)
    if account_id:
        query = query.where(Run.account_id == account_id)
    if task_id:
        query = query.where(Run.task_id == task_id)
    if status:
        query = query.where(Run.status == status)
    if q:
        query = query.where(
            or_(
                Run.summary.contains(q, autoescape=True),
                Run.response.contains(q, autoescape=True),
                Run.task_name.contains(q, autoescape=True),
            )
        )
    with Session() as db:
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = db.scalars(
            query.order_by(Run.id.desc()).offset((page - 1) * size).limit(size)
        ).all()
        return {
            "items": [run_dict(r) for r in items],
            "total": total,
            "page": page,
            "size": size,
        }


@api.get("/overview", tags=["概览"])
def overview():
    tz = ZoneInfo(get_setting("timezone", "Asia/Shanghai"))
    today = datetime.now(tz).date()
    start = (
        datetime.combine(today - timedelta(days=6), datetime.min.time(), tzinfo=tz)
        .astimezone(timezone.utc)
        .isoformat()
    )
    with Session() as db:
        trend = {
            str(today - timedelta(days=6 - i)): {
                "date": str(today - timedelta(days=6 - i)),
                "success": 0,
                "failed": 0,
                "completed": 0,
            }
            for i in range(7)
        }
        boundaries = [
            (datetime.combine(datetime.fromisoformat(day).date(), datetime.min.time(), tzinfo=tz)
             .astimezone(timezone.utc).isoformat(), day)
            for day in reversed(trend)
        ]
        day_key = case(*[(Run.started_at >= boundary, day) for boundary, day in boundaries])
        rows = db.execute(
            select(day_key, Run.status, func.count(Run.id))
            .where(Run.started_at >= start).group_by(day_key, Run.status)
        ).all()
        today_runs = 0
        for key, status, count in rows:
            if key == str(today):
                today_runs += count
            if key in trend and status in trend[key]:
                trend[key][status] += count
        day = trend[str(today)]
        verified = day["success"] + day["failed"]
        next_times = [
            datetime.fromisoformat(value)
            for value in db.scalars(
                select(Task.next_run).where(
                    Task.enabled.is_(True), Task.kind == "signer", Task.next_run.is_not(None)
                )
            )
        ]
        return {
            "accounts": db.scalar(select(func.count(Account.id))),
            "active_tasks": db.scalar(select(func.count(Task.id)).where(Task.enabled.is_(True))),
            "success_rate": round(day["success"] / verified * 100, 1) if verified else None,
            "today_runs": today_runs,
            "next_run": min(next_times).isoformat() if next_times else None,
            "trend": list(trend.values()),
            "recent": [
                {c.name: getattr(r, c.name) for c in Run.__table__.columns if c.name != "response"}
                for r in db.scalars(select(Run).options(defer(Run.response))
                                    .order_by(Run.id.desc()).limit(6))
            ],
            "timezone": str(tz),
            "running": len(task_engine.running),
            "notifications_enabled": any(
                item.get("enabled", True)
                for item in json.loads(decrypt(get_setting("notifications")) or "[]")
            ),
        }


@api.get("/logs/stream", tags=["日志"], summary="EventSource 日志流；WebSocket 不可用时使用")
async def stream_logs(request: Request):
    async def events():
        queue = asyncio.Queue(maxsize=200)
        bus.clients.add(queue)
        try:
            yield (
                "data: "
                + json.dumps({"type": "history", "items": list(bus.history)}, ensure_ascii=False)
                + "\n\n"
            )
            while not await request.is_disconnected():
                if not valid_token(request.cookies.get(COOKIE)):
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), 20)
                    yield (
                        "data: "
                        + json.dumps({"type": "log", "item": entry}, ensure_ascii=False)
                        + "\n\n"
                    )
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            bus.clients.discard(queue)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
    )


@app.websocket("/ws/logs")
async def live_logs(ws: WebSocket):
    origin = ws.headers.get("origin")
    if not valid_token(ws.cookies.get(COOKIE)) or (
        origin and urlparse(origin).netloc != ws.headers.get("host")
    ):
        await ws.close(code=4401)
        return
    await ws.accept()
    queue = asyncio.Queue(maxsize=200)
    bus.clients.add(queue)

    async def send():
        await ws.send_json({"type": "history", "items": list(bus.history)})
        while True:
            if not valid_token(ws.cookies.get(COOKIE)):
                await ws.close(code=4401)
                break
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=20)
                await ws.send_json({"type": "log", "item": entry})
            except TimeoutError:
                await ws.send_json({"type": "ping"})

    async def receive():
        while True:
            await ws.receive_text()

    futures = [asyncio.create_task(send()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(futures, return_when=asyncio.FIRST_COMPLETED)
        for future in done:
            future.result()
    except (WebSocketDisconnect, RuntimeError, OSError, asyncio.CancelledError):
        pass
    finally:
        for future in futures:
            future.cancel()
        await asyncio.gather(*futures, return_exceptions=True)
        bus.clients.discard(queue)


MASK = "••••••••"


def masked_config(value):
    if isinstance(value, list):
        return [masked_config(v) for v in value]
    if isinstance(value, dict):
        return {
            k: MASK if k in {"key", "token", "headers"} and v else masked_config(v)
            for k, v in value.items()
        }
    return value


def restore_mask(value, old):
    if value == MASK:
        return old
    if isinstance(value, list):
        return [
            restore_mask(v, old[i] if isinstance(old, list) and i < len(old) else None)
            for i, v in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            k: restore_mask(v, old.get(k) if isinstance(old, dict) else None)
            for k, v in value.items()
        }
    return value


class SettingsInput(BaseModel):
    global_proxy: str = ""
    timezone: str = "Asia/Shanghai"
    ai_key: str = ""
    ai_base_url: str = ""
    ai_model: str = "gpt-4o"
    notifications: list[dict] = Field(default_factory=list, max_length=20)


@api.get("/settings", tags=["系统设置"])
def settings():
    return {
        "global_proxy": MASK if get_setting("global_proxy") else "",
        "proxy_display": proxy_label(decrypt(get_setting("global_proxy"))),
        "timezone": get_setting("timezone", "Asia/Shanghai"),
        "ai_key": MASK if get_setting("ai_key") else "",
        "ai_base_url": get_setting("ai_base_url"),
        "ai_model": get_setting("ai_model", "gpt-4o"),
        "notifications": masked_config(json.loads(decrypt(get_setting("notifications")) or "[]")),
        "tg_signer_version": upstream_version,
        "upstream_commit": "fc8905db2ee74e2d9afc135cce88b50be35b6212",
    }


@api.put(
    "/settings",
    tags=["系统设置"],
    summary="保存加密的代理、AI 与通知配置；掩码表示保留原值",
)
async def update_settings(data: SettingsInput):
    ZoneInfo(data.timezone)
    if data.global_proxy != MASK:
        validate_proxy(data.global_proxy)
    old = json.loads(decrypt(get_setting("notifications")) or "[]")
    configs = restore_mask(data.notifications, old)
    required = {
        "bark": ["url", "key"],
        "telegram": ["token", "chat_id"],
        "serverchan": ["key"],
        "pushdeer": ["key"],
        "webhook": ["url"],
    }
    for item in configs:
        kind = item.get("type")
        if kind not in required or any(not item.get(k) for k in required[kind]):
            raise HTTPException(422, f"通知配置缺少必要字段：{kind}")
        if item.get("url") and urlparse(item["url"]).scheme not in {"http", "https"}:
            raise HTTPException(422, "通知地址必须是 HTTP(S) URL")
    if data.ai_base_url and urlparse(data.ai_base_url).scheme not in {"http", "https"}:
        raise HTTPException(422, "AI Base URL 必须是 HTTP(S) URL")
    proxy_changed = data.global_proxy != MASK and data.global_proxy != decrypt(
        get_setting("global_proxy")
    )
    for key in ["global_proxy", "ai_key"]:
        value = getattr(data, key)
        if value != MASK:
            set_setting(key, encrypt(value))
    for key in ["timezone", "ai_base_url", "ai_model"]:
        set_setting(key, getattr(data, key))
    set_setting("notifications", encrypt(json.dumps(configs)))
    if proxy_changed:
        for account_id in list(telegram.clients):
            await reconnect_account(account_id, connect=False)
    return settings()


@api.post("/settings/test-notification", tags=["系统设置"], summary="向已配置渠道发送测试通知")
async def test_notification():
    return await notify("tg-signer 测试通知", "通知通道已连通。")


@api.get("/capabilities", tags=["上游兼容"])
def capabilities():
    register_builtin_handlers()
    return {
        "handlers": list(list_handlers()),
        "schemas": {
            "signer": SignConfigV3.model_json_schema(),
            "automation": AutomationConfig.model_json_schema(),
            "monitor": MonitorConfig.model_json_schema(),
        },
        "version": upstream_version,
    }


@api.post("/config/parse", tags=["上游兼容"], summary="解析导入的 JSON / YAML 配置")
async def parse_config(file: UploadFile = File(...)):
    content = await file.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(413, "配置文件过大")
    try:
        result = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(400, "配置语法错误") from exc
    if not isinstance(result, dict):
        raise HTTPException(400, "配置必须是 JSON/YAML 对象")
    return result


@api.post(
    "/tools",
    tags=["Telegram 工具"],
    summary="对话/Folder/话题/成员查询、发消息、骰子、Telegram 定时消息与注销",
)
async def tools(data: ToolInput):
    async with telegram.execution_locks[data.account_id]:
        worker = await telegram.worker(data.account_id)
        client = worker.app
        chat = parse_chat_id_or_username(data.chat_id) if data.chat_id else None
        if data.operation not in {"dialogs", "folders", "logout"} and chat is None:
            raise HTTPException(422, "此操作需要目标 Chat ID / @username")

        async def call(name, fn):
            return await worker._call_telegram_api(name, fn)

        async def collect(iterator):
            return [item async for item in iterator]

        async with asyncio.timeout(180):
            if data.operation == "dialogs":
                if data.folder:
                    folders = await call("folders", client.get_folders)
                    result = _explicit_folder_chats(_select_chat_folder(folders, data.folder))
                else:
                    result = [
                        d.chat
                        for d in await call(
                            "dialogs",
                            lambda: collect(client.get_dialogs(limit=data.limit)),
                        )
                    ]
            elif data.operation == "folders":
                result = await call("folders", client.get_folders)
            elif data.operation == "topics":
                result = await worker.get_forum_topics(chat, data.limit)
            elif data.operation == "members":
                result = await call(
                    "members",
                    lambda: collect(
                        client.get_chat_members(
                            chat,
                            query=data.query,
                            limit=data.limit,
                            filter=ChatMembersFilter.ADMINISTRATORS
                            if data.admin
                            else ChatMembersFilter.SEARCH,
                        )
                    ),
                )
            elif data.operation == "send_text":
                if not data.text:
                    raise HTTPException(422, "消息不能为空")
                result = await worker.send_message(
                    chat,
                    data.text,
                    data.delete_after,
                    message_thread_id=data.message_thread_id,
                )
            elif data.operation == "send_dice":
                result = await worker.send_dice(
                    chat,
                    data.text or "🎲",
                    data.delete_after,
                    message_thread_id=data.message_thread_id,
                )
            elif data.operation == "schedule":
                from apscheduler.triggers.cron import CronTrigger

                CronTrigger.from_crontab(data.cron)
                if not data.text:
                    raise HTTPException(422, "消息不能为空")
                result = await worker.schedule_messages(
                    chat,
                    data.text,
                    data.cron,
                    data.next_times,
                    data.random_seconds,
                    data.message_thread_id,
                )
            elif data.operation == "scheduled":
                result = await call("scheduled", lambda: client.get_scheduled_messages(chat))
            elif data.operation == "delete_scheduled":
                result = await call(
                    "delete_scheduled",
                    lambda: client.delete_scheduled_messages(chat, data.message_ids),
                )
            else:
                with Session() as db:
                    ids = list(
                        db.scalars(select(Task.id).where(Task.account_id == data.account_id))
                    )
                    for task_id in ids:
                        db.get(Task, task_id).enabled = False
                    db.commit()
                for task_id in ids:
                    await task_engine.reconcile(task_id)
                result = await call("logout", client.log_out)
                await telegram.close(data.account_id)
                with Session() as db:
                    a = get_account(db, data.account_id)
                    a.status = "invalid"
                    db.commit()

        def serialize(value):
            if isinstance(value, (list, tuple)):
                return [serialize(v) for v in value]
            if value is None or isinstance(value, (str, int, float, bool, dict)):
                return value
            try:
                return json.loads(str(value))
            except (ValueError, TypeError):
                return str(value)

        return {"result": serialize(result)}


@api.get("/records/upstream", tags=["上游兼容"])
def upstream_records(limit: int = Query(100, ge=1, le=1000)):
    return [
        dataclasses.asdict(r) for r in SignRecordStore(DATA / "upstream").list_recent_records(limit)
    ]


@api.post(
    "/records/migrate",
    tags=["上游兼容"],
    summary="将挂载工作目录中的旧版 sign_record.json 迁移至上游 SQLite",
)
def migrate_records(legacy_user_id: str | None = None):
    return dataclasses.asdict(
        SignRecordStore(DATA / "upstream").migrate_all_json_records(
            legacy_user_id=legacy_user_id, remove_files=False
        )
    )


@api.get("/plugins", tags=["上游兼容"], summary="列出自定义 Python handler 插件")
def plugins():
    directory = DATA / "upstream" / "handlers"
    directory.mkdir(parents=True, exist_ok=True)
    return [{"name": p.name, "code": p.read_text()} for p in directory.glob("*.py") if p.is_file()]


class PluginInput(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,60}\.py$")
    code: str = Field(max_length=200000)


@api.put(
    "/plugins",
    tags=["上游兼容"],
    summary="安装可信的 Python handler 插件；以容器用户权限执行",
)
def save_plugin(data: PluginInput):
    try:
        compile(data.code, data.name, "exec")
    except SyntaxError as exc:
        raise HTTPException(422, f"Python 语法错误，第 {exc.lineno} 行：{exc.msg}") from exc
    directory = DATA / "upstream" / "handlers"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / data.name).write_text(data.code)
    return {"status": "saved", "message": "重新启动自动化任务后生效"}


@api.delete("/plugins/{name}", tags=["上游兼容"])
def delete_plugin(name: str):
    if not __import__("re").fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,60}\.py", name):
        raise HTTPException(400, "文件名无效")
    (DATA / "upstream" / "handlers" / name).unlink(missing_ok=True)
    return {"status": "deleted"}


@api.get("/openapi.json", include_in_schema=False)
def openapi():
    return app.openapi()


@api.get("/docs", include_in_schema=False)
def docs():
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="tg-signer API")


api.include_router(panel_private_router)
api.include_router(backup_router)
api.include_router(performance_router)
api.include_router(task_admin_router)
api.include_router(relay_full_preview_router)
api.include_router(relay_backfill_router)
api.include_router(relay_history_router)
api.include_router(relay_operations_router)
api.include_router(relay_p7_router)
api.include_router(relay_retry_router)
api.include_router(maintenance_router)
app.include_router(api)
static = Path(os.getenv("STATIC_DIR", "frontend/out"))
if static.is_dir():
    app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
