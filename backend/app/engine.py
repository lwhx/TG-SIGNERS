import asyncio
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import regex
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from croniter import croniter
from fastapi import HTTPException
from pyrogram import filters
from pyrogram.handlers import EditedMessageHandler, MessageHandler
from sqlalchemy import select, update
from tg_signer.automation import handlers as upstream_handlers
from tg_signer.automation.engine import UserAutomation
from tg_signer.automation.handlers import load_plugins, register_builtin_handlers
from tg_signer.config import AutomationConfig, MonitorConfig, SignConfigV3
from tg_signer.core import (
    UserMonitor,
    _explicit_folder_chats,
    _select_chat_folder,
    logger,
)

from .db import DATA, Account, Run, Session, Task, now
from .events import bus, execution_errors, log_context
from .notifications import notify
from .telegram import AIMixin, telegram


class AuditMixin:
    async def audit(self, operation, label, text, *, relay_rule=False):
        if not self.active:
            return
        with Session() as db:
            task = db.get(Task, int(self.task_name))
            account = db.get(Account, self.account_id)
            if not task or not account:
                return
            record = Run(
                task_id=task.id,
                account_id=account.id,
                task_name=f"{task.name} · {label}",
                account_name=account.name,
            )
            db.add(record)
            db.commit()
            run_id = record.id
        ctx = log_context.set({"task_id": task.id, "account_id": account.id, "run_id": run_id})
        captured = []
        error_ctx = execution_errors.set(captured)
        status, summary = "completed", "规则处理完成"

        async def guarded():
            async with self.audit_locks[label]:
                if self.active:
                    await operation()

        future = asyncio.create_task(guarded())
        self.inflight.add(future)
        try:
            await future
            if captured:
                status, summary = "failed", "; ".join(captured)[:3000]
        except asyncio.CancelledError:
            status, summary = "cancelled", "规则已中断"
            if self.active:
                raise
        except Exception as exc:
            status, summary = "failed", f"{type(exc).__name__}: {str(exc)[:500]}"
        finally:
            self.inflight.discard(future)
            with Session() as db:
                record = db.get(Run, run_id)
                record.status, record.summary, record.response, record.finished_at = (
                    status,
                    summary,
                    text[:200000],
                    now(),
                )
                db.commit()
            bus.emit(summary, "ERROR" if status == "failed" else "INFO")
            if status == "failed" and not (
                relay_rule and captured and all(
                    "relay_message: Telegram delivery failed (" in msg for msg in captured
                )
            ):
                await notify("自动化规则失败", f"{task.name} · {label}: {summary}")
            execution_errors.reset(error_ctx)
            log_context.reset(ctx)


class Automation(AuditMixin, AIMixin, UserAutomation):
    async def _run_rule(self, rule, event):
        parent = super()._run_rule
        text = str((event.message.text or event.message.caption or "") if event.message else "")
        await self.audit(
            lambda: parent(rule, event), rule.id, text,
            relay_rule=any(h.handler == "relay_message" for h in rule.handlers),
        )


class Monitor(AuditMixin, AIMixin, UserMonitor):
    async def on_message(self, client, message):
        if not any(cfg.match(message) for cfg in self.config.match_cfgs):
            return
        parent = super().on_message
        await self.audit(
            lambda: parent(client, message), "消息匹配", str(message.text or message.caption or "")
        )


def matches(pattern, text):
    return bool(pattern and regex.search(pattern, text, flags=regex.IGNORECASE, timeout=0.1))


class TaskEngine:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.running = {}
        self.listener_ready = set()
        self.stopping = False

    async def start(self):
        self.stopping = False
        with Session() as db:
            db.execute(
                update(Run)
                .where(Run.status == "running")
                .values(
                    status="interrupted",
                    finished_at=now(),
                    summary="服务重启，前一次执行已中断",
                )
            )
            db.execute(
                update(Task).where(Task.last_status == "running").values(last_status="interrupted")
            )
            tasks = db.scalars(select(Task).where(Task.enabled.is_(True))).all()
            db.commit()
        self.scheduler.start()
        for task in tasks:
            if task.kind == "signer":
                self.schedule(task.id, restore=True)
            else:
                self.launch(task.id)

    def schedule(self, task_id, restore=False):
        job_id = f"task-{task_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
        with Session() as db:
            task = db.get(Task, task_id)
            if not task or not task.enabled or task.kind != "signer":
                return
            current = datetime.now(timezone.utc)
            next_run = datetime.fromisoformat(task.next_run) if restore and task.next_run else None
            if next_run is None or next_run < current - timedelta(minutes=5):
                base = croniter(task.cron, current.astimezone(ZoneInfo(task.timezone))).get_next(
                    datetime
                )
                next_run = base + timedelta(seconds=random.randint(task.delay_min, task.delay_max))
            task.next_run = next_run.isoformat()
            db.commit()
        self.scheduler.add_job(
            self.scheduled,
            "date",
            run_date=next_run,
            args=[task_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,
        )

    async def scheduled(self, task_id):
        try:
            self.launch(task_id)
        except HTTPException:
            bus.emit("任务仍在执行，跳过本次重复触发", "WARNING", task_id=task_id)
        finally:
            self.schedule(task_id)

    def launch(self, task_id):
        if self.stopping:
            raise HTTPException(503, "服务正在关闭")
        if task_id in self.running:
            raise HTTPException(409, "任务正在运行")
        with Session() as db:
            if not db.get(Task, task_id):
                raise HTTPException(404, "任务不存在")
        future = asyncio.create_task(self.execute(task_id), name=f"task-{task_id}")
        self.running[task_id] = future

        def finished(done):
            if self.running.get(task_id) is done:
                self.running.pop(task_id, None)

        future.add_done_callback(finished)
        return {"status": "queued", "task_id": task_id}

    async def stop(self, task_id):
        future = self.running.get(task_id)
        if future:
            future.cancel()
            await asyncio.gather(future, return_exceptions=True)

    async def reconcile(self, task_id):
        await self.stop(task_id)
        job = self.scheduler.get_job(f"task-{task_id}")
        if job:
            job.remove()
        with Session() as db:
            task = db.get(Task, task_id)
        if task and task.enabled:
            if task.kind == "signer":
                self.schedule(task_id)
            else:
                self.launch(task_id)

    async def execute(self, task_id):
        with Session() as db:
            task = db.get(Task, task_id)
            if not task:
                return
            account = db.get(Account, task.account_id)
            record = Run(
                task_id=task.id,
                task_name=task.name,
                account_id=task.account_id,
                account_name=account.name,
            )
            db.add(record)
            task.last_run, task.last_status = now(), "running"
            db.commit()
            run_id = record.id
        ctx = log_context.set({"task_id": task.id, "account_id": task.account_id, "run_id": run_id})
        status, summary, response = "failed", "", ""
        bus.emit(f"开始执行 {task.name}")
        try:
            if task.kind == "signer":
                async with telegram.execution_locks[task.account_id]:
                    async with asyncio.timeout(3600):
                        status, summary, response = await self.sign(task)
            else:
                await self.resident(task)
                status, summary = "completed", "监听结束"
        except asyncio.CancelledError:
            status, summary = "cancelled", "执行已停止"
        except Exception as exc:
            summary = f"{type(exc).__name__}: {str(exc)[:500]}"
            if type(exc).__module__.startswith("pyrogram"):
                await telegram.mark_error(task.account_id, exc)
        finally:
            with Session() as db:
                record = db.get(Run, run_id)
                record.status, record.summary, record.response, record.finished_at = (
                    status,
                    summary,
                    response,
                    now(),
                )
                current = db.get(Task, task_id)
                if current:
                    current.last_status, current.last_summary = status, summary
                db.commit()
            bus.emit(f"{task.name} · {summary}", "ERROR" if status == "failed" else "INFO")
            if status == "failed":
                await notify("签到 / 自动化任务失败", f"{task.name}: {summary}")
            log_context.reset(ctx)
        if task.kind != "signer" and status == "failed" and not self.stopping:
            with Session() as db:
                current = db.get(Task, task_id)
                enabled = current and current.enabled
            if enabled:
                self.scheduler.add_job(
                    self.restart_resident,
                    "date",
                    run_date=datetime.now(timezone.utc) + timedelta(seconds=60),
                    args=[task_id],
                    id=f"task-{task_id}",
                    replace_existing=True,
                )

    async def restart_resident(self, task_id):
        with Session() as db:
            task = db.get(Task, task_id)
        if task and task.enabled and task_id not in self.running:
            self.launch(task_id)

    async def discover(self, worker, folder):
        if folder:
            folders = await worker._call_telegram_api("get_folders", worker.app.get_folders)
            _explicit_folder_chats(_select_chat_folder(folders, folder))
        else:

            async def load():
                return [d async for d in worker.app.get_dialogs(limit=100)]

            await worker._call_telegram_api("get_dialogs", load)

    async def sign(self, task):
        worker = await telegram.worker(task.account_id, task_id=task.id)
        worker.config = SignConfigV3.model_validate(task.config)
        if worker.config.requires_ai:
            worker.ensure_ai_cfg()
        await self.discover(worker, task.folder)
        all_responses, failures = [], []
        group = 1000 + task.id
        active_route = None
        received = {}
        changed = asyncio.Event()

        def callback_text(text):
            received[-len(received) - 1] = text
            bus.emit(text, task_id=task.id, account_id=task.account_id)
            changed.set()

        worker.on_callback_text = callback_text

        async def incoming(client, message):
            route = (message.chat.id, getattr(message, "message_thread_id", None))
            if (
                not active_route
                or route[0] != active_route[0]
                or (active_route[1] is not None and route[1] != active_route[1])
            ):
                return
            if getattr(message, "outgoing", False):
                return
            await worker._on_message(client, message)
            text = str(message.text or message.caption or "")
            received[message.id] = text
            bus.emit(text or "[收到媒体消息]", task_id=task.id, account_id=task.account_id)
            changed.set()

        handlers = [
            MessageHandler(incoming, filters.all),
            EditedMessageHandler(incoming, filters.all),
        ]
        for handler in handlers:
            worker.app.add_handler(handler, group=group)
        # Dispatcher installs handlers in a scheduled coroutine.
        await asyncio.sleep(0)
        try:
            for chat in worker.config.chats:
                active_route = await worker.resolve_chat_route_key(chat)
                worker.context.sign_chats[active_route].append(chat)
                received.clear()
                changed.clear()
                worker.warnings.clear()
                await worker.sign_a_chat(chat)
                deadline = asyncio.get_running_loop().time() + task.response_timeout
                while True:
                    raw = "\n".join(received.values())
                    if matches(task.failure_pattern, raw) or matches(task.success_pattern, raw):
                        break
                    if received and not task.success_pattern:
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    changed.clear()
                    try:
                        await asyncio.wait_for(changed.wait(), remaining)
                    except TimeoutError:
                        break
                raw = "\n".join(received.values())
                all_responses.append(f"[{chat.chat_id}]\n{raw}")
                if matches(task.failure_pattern, raw):
                    failures.append(f"{chat.chat_id}: 命中失败规则")
                elif task.success_pattern and not matches(task.success_pattern, raw):
                    failures.append(f"{chat.chat_id}: 未匹配成功回复")
                elif worker.warnings:
                    failures.append(f"{chat.chat_id}: " + "; ".join(worker.warnings))
                worker.context.sign_chats.pop(active_route, None)
                worker.context.chat_messages.pop(active_route, None)
                await asyncio.sleep(max(0, worker.config.sign_interval))
        finally:
            for handler in handlers:
                worker.app.remove_handler(handler, group=group)
        response = "\n\n".join(all_responses)[:200000]
        if failures:
            return "failed", "; ".join(failures)[:3000], response
        worker.persist_sign_record(worker.load_sign_record(), str(datetime.now().date()), now())
        return (
            ("success", "所有目标均匹配成功规则", response)
            if task.success_pattern
            else ("completed", "动作已完成（未配置成功匹配规则）", response)
        )

    async def resident(self, task):
        cls = Automation if task.kind == "automation" else Monitor
        worker = await telegram.worker(task.account_id, cls, task.id)
        worker.account_id = task.account_id
        worker.active = True
        worker.inflight = set()
        worker.audit_locks = defaultdict(asyncio.Lock)
        worker.config = (
            AutomationConfig if task.kind == "automation" else MonitorConfig
        ).model_validate(task.config)
        if worker.config.requires_ai:
            worker.ensure_ai_cfg()
        await self.discover(worker, task.folder)
        group = 1000 + task.id
        pending = []
        if task.kind == "automation":
            # Plugin functions share one registry in the upstream engine. Rebuild
            # it synchronously so edited/deleted files cannot leave stale handlers.
            upstream_handlers._REGISTRY.clear()
            register_builtin_handlers()
            load_plugins(DATA / "upstream" / "handlers", logger)
            handlers = [
                MessageHandler(worker.on_message, filters.all),
                EditedMessageHandler(worker.on_edited_message, filters.all),
            ]
        else:
            handlers = [
                MessageHandler(
                    worker.on_message,
                    filters.text & filters.chat(worker.config.chat_ids),
                )
            ]
        for handler in handlers:
            worker.app.add_handler(handler, group=group)
        try:
            if task.kind == "automation":
                pending = [
                    asyncio.create_task(worker.run_startup(rule))
                    for rule in worker.config.rules
                    if rule.enabled and worker._has_trigger(rule, "startup")
                ]
                pending.append(asyncio.create_task(worker.timer_loop()))
            self.listener_ready.add(task.id)
            bus.emit("监听器已启动", task_id=task.id)
            # Propagate timer failures; message callbacks are handled by upstream.
            if pending:
                await asyncio.gather(*pending)
            else:
                await asyncio.Event().wait()
        finally:
            self.listener_ready.discard(task.id)
            worker.active = False
            for handler in handlers:
                worker.app.remove_handler(handler, group=group)
            for future in list(worker.inflight):
                future.cancel()
            await asyncio.gather(*list(worker.inflight), return_exceptions=True)
            for future in pending:
                future.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if task.kind == "automation":
                worker.state.save(force=True)

    async def shutdown(self):
        self.stopping = True
        self.scheduler.shutdown(wait=False)
        for task_id in list(self.running):
            await self.stop(task_id)
        for account_id in list(telegram.clients):
            await telegram.close(account_id)


task_engine = TaskEngine()
