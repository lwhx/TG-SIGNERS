"""Share one live client per account with tg-signer's rate-limited API wrappers."""

import asyncio
from collections import defaultdict

from fastapi import HTTPException
from pyrogram import errors
from tg_signer import core
from tg_signer.ai_tools import AITools
from tg_signer.automation.delivery_outcome import relay_attempt, relay_rpc_state

from .auth import disconnect
from .db import DATA, Account, Session, get_setting, now
from .events import bus
from .notifications import notify
from .security import decrypt


class HeadlessClient(core.Client):
    relay_single_attempt = True

    async def invoke(self, query, *args, **kwargs):
        if relay_attempt.get():
            kwargs.update(retries=1, sleep_threshold=0)
        result = await super().invoke(query, *args, **kwargs)
        state = relay_rpc_state.get()
        if state is not None and getattr(query, "QUALNAME", "").rsplit(".", 1)[-1] in {
            "SendMessage",
            "SendMedia",
            "SendMultiMedia",
            "ForwardMessages",
        }:
            state["accepted"] = True
        return result

    async def start(self):
        if not self.is_connected:
            authorized = await self.connect()
            if not authorized:
                await self.disconnect()
                raise errors.AuthKeyUnregistered()
        self.me = await self.get_me()
        if not self.is_initialized:
            await self.initialize()
        return self

    async def save_session_string(self):
        # Session material is persisted encrypted by the dashboard only.
        return None


class AIMixin:
    def ensure_ai_cfg(self):
        if not decrypt(get_setting("ai_key")):
            raise ValueError("请先在系统设置中配置 AI API Key")

    def get_ai_tools(self):
        self.ensure_ai_cfg()
        return AITools(
            {
                "api_key": decrypt(get_setting("ai_key")),
                "base_url": get_setting("ai_base_url") or None,
                "model": get_setting("ai_model", "gpt-4o"),
            }
        )


class Signer(AIMixin, core.UserSigner):
    def load_config(self, cfg_cls=None):
        return self.config

    def log(self, msg, level="INFO", **kwargs):
        if level.upper() in {"WARNING", "ERROR", "CRITICAL"}:
            self.warnings.append(str(msg))
        super().log(msg, level, **kwargs)

    async def request_callback_answer(self, client, chat_id, message_id, callback_data, **kwargs):
        result = await self._call_telegram_api(
            "messages.GetBotCallbackAnswer",
            lambda: client.request_callback_answer(
                chat_id, message_id, callback_data=callback_data, **kwargs
            ),
        )
        if getattr(result, "text", None) and hasattr(self, "on_callback_text"):
            self.on_callback_text(str(result.text))
        self.log("点击完成")
        return result


class TelegramManager:
    def __init__(self):
        self.clients = {}
        self.locks = defaultdict(asyncio.Lock)
        self.execution_locks = defaultdict(asyncio.Lock)

    async def client(self, account_id):
        async with self.locks[account_id]:
            if account_id in self.clients:
                return self.clients[account_id]
            with Session() as db:
                account = db.get(Account, account_id)
                if not account:
                    raise HTTPException(404, "账号不存在")
            proxy = decrypt(account.proxy) or decrypt(get_setting("global_proxy"))
            client = HeadlessClient(
                f"account-{account.id}",
                api_id=account.api_id,
                api_hash=decrypt(account.api_hash),
                session_string=decrypt(account.session),
                in_memory=True,
                workdir=DATA,
                proxy=core.get_proxy(proxy),
                workers=8,
            )
            try:
                async with asyncio.timeout(45):
                    await client.__aenter__()  # retain one lifecycle reference
            except BaseException as exc:
                core._CLIENT_REFS.pop(client.key, None)
                await disconnect(client)
                await self.mark_error(account_id, exc)
                raise
            core._CLIENT_INSTANCES[client.key] = client
            self.clients[account_id] = client
            with Session() as db:
                account = db.get(Account, account_id)
                account.status, account.last_checked = "online", now()
                db.commit()
            return client

    async def mark_error(self, account_id, exc):
        banned = type(exc).__name__ in {
            "UserDeactivatedBan",
            "PhoneNumberBanned",
            "UserDeactivated",
        }
        invalid = isinstance(exc, errors.Unauthorized)
        if (
            not banned
            and not invalid
            and not isinstance(exc, (ConnectionError, OSError, TimeoutError))
        ):
            return
        status = "banned" if banned else "invalid" if invalid else "offline"
        with Session() as db:
            a = db.get(Account, account_id)
            if a:
                changed = a.status != status
                a.status, a.last_checked = status, now()
                name = a.name
                db.commit()
                if changed and (banned or invalid):
                    await notify("Telegram 会话失效", f"账号 {name} 需要重新验证。")

    async def close(self, account_id):
        async with self.locks[account_id]:
            client = self.clients.pop(account_id, None)
            if client:
                await disconnect(client)
                core._CLIENT_INSTANCES.pop(client.key, None)
                core._CLIENT_REFS.pop(client.key, None)
                core._LOGIN_USERS.pop(client.key, None)

    async def worker(self, account_id, cls=Signer, task_id="tools"):
        client = await self.client(account_id)
        worker = cls(
            task_name=str(task_id),
            account=client.name,
            session_dir=str(DATA),
            workdir=str(DATA / "upstream"),
            session_string=client.session_string,
            in_memory=True,
            loop=asyncio.get_running_loop(),
        )
        worker.user = client.me
        worker.warnings = []
        return worker

    async def ping(self, account_id):
        try:
            worker = await self.worker(account_id)
            async with asyncio.timeout(45):
                me = await worker._call_telegram_api("get_me", worker.app.get_me)
                value = await worker.app.export_session_string()
            from .security import encrypt

            with Session() as db:
                a = db.get(Account, account_id)
                a.status, a.last_checked = "online", now()
                a.username, a.phone, a.session = (
                    me.username or "",
                    "+" + (me.phone_number or "").lstrip("+"),
                    encrypt(value),
                )
                db.commit()
            bus.emit("账号连接验证成功", account_id=account_id)
            return {"status": "online", "user_id": me.id}
        except Exception as exc:
            await self.mark_error(account_id, exc)
            raise


telegram = TelegramManager()
