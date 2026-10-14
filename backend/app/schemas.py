from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import regex
from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator
from tg_signer.config import (
    AutomationConfig,
    MonitorConfig,
    SignConfigV1,
    SignConfigV2,
    SignConfigV3,
)

from .relay import validate_relay_config


def validate_proxy(value):
    if value:
        u = urlparse(value)
        if u.scheme not in {"socks5", "socks4", "http"} or not u.hostname or not u.port:
            raise ValueError("代理格式应为 socks5://host:port 或 http://host:port")
    return value


class SendCode(BaseModel):
    phone: str = Field(pattern=r"^\+[1-9]\d{6,14}$")
    name: str = Field(default="", max_length=100)
    api_id: int | None = Field(default=None, gt=0)
    api_hash: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{32}$")
    proxy: str = Field(default="", max_length=1000)
    flow_id: str | None = None

    _proxy = field_validator("proxy")(validate_proxy)

    @model_validator(mode="after")
    def pair(self):
        if bool(self.api_id) != bool(self.api_hash):
            raise ValueError("API_ID 与 API_HASH 必须同时填写")
        return self


class SignIn(BaseModel):
    flow_id: str
    code: str = Field(pattern=r"^\d{4,8}$")
    phone_code_hash: str


class Check2FA(BaseModel):
    flow_id: str
    password: str = Field(min_length=1, max_length=256)


class TaskInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    account_id: int
    kind: Literal["signer", "automation", "monitor"] = "signer"
    enabled: bool = True
    cron: str = "0 8 * * *"
    timezone: str = "Asia/Shanghai"
    delay_min: int = Field(default=10, ge=0, le=86400)
    delay_max: int = Field(default=300, ge=0, le=86400)
    success_pattern: str = Field(default="", max_length=1000)
    failure_pattern: str = Field(default="", max_length=1000)
    response_timeout: int = Field(default=20, ge=1, le=300)
    folder: str = Field(default="", max_length=100)
    config: dict

    @model_validator(mode="after")
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("无效的 IANA 时区") from exc
        if len(self.cron.split()) != 5 or not croniter.is_valid(self.cron):
            raise ValueError("请输入有效的五段 Cron 表达式")
        if self.delay_max < self.delay_min:
            raise ValueError("随机延迟上限不能小于下限")
        for pattern in [self.success_pattern, self.failure_pattern]:
            if pattern:
                try:
                    regex.compile(pattern)
                except regex.error as exc:
                    raise ValueError(f"正则表达式无效：{exc}") from exc
        model = {
            "signer": SignConfigV3,
            "automation": AutomationConfig,
            "monitor": MonitorConfig,
        }[self.kind]
        loaded = model.load(self.config)
        if not loaded and self.kind == "signer":
            legacy = SignConfigV1.valid(self.config)
            if legacy:
                loaded = (SignConfigV2.to_current(SignConfigV1.to_current(legacy)), True)
        if not loaded:
            model.model_validate(self.config)  # return the useful validation error
            raise ValueError("配置版本无法识别")
        cfg = loaded[0]
        if self.kind == "signer" and not cfg.chats:
            raise ValueError("至少添加一个目标对话")
        if self.kind == "signer":
            if not 0 <= cfg.sign_interval <= 3600:
                raise ValueError("目标间隔应为 0～3600 秒")
            for chat in cfg.chats:
                if not chat.actions:
                    raise ValueError("每个目标至少需要一个动作")
                if not 0 <= chat.action_interval <= 3600:
                    raise ValueError("动作间隔应为 0～3600 秒")
                if chat.delete_after is not None and not 0 <= chat.delete_after <= 3600:
                    raise ValueError("删除延迟应为 0～3600 秒")
        if self.kind == "automation" and not cfg.rules:
            raise ValueError("至少添加一条自动化规则")
        if self.kind == "monitor" and not cfg.match_cfgs:
            raise ValueError("至少添加一条监控规则")
        normalized = cfg.model_dump(mode="json")
        if self.kind == "automation":
            validate_relay_config(normalized)
        self.config = normalized
        return self


class PanelLogin(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


class AccountUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    proxy: str | None = None

    @field_validator("proxy")
    @classmethod
    def valid(cls, value):
        return validate_proxy(value) if value is not None else value


class ToolInput(BaseModel):
    account_id: int
    operation: Literal[
        "dialogs",
        "folders",
        "topics",
        "members",
        "send_text",
        "send_dice",
        "schedule",
        "scheduled",
        "delete_scheduled",
        "logout",
    ]
    chat_id: str = ""
    text: str = Field(default="", max_length=4096)
    folder: str = ""
    message_thread_id: int | None = Field(default=None, gt=0)
    delete_after: int | None = Field(default=None, ge=0, le=3600)
    limit: int = Field(default=50, ge=1, le=500)
    admin: bool = False
    query: str = ""
    cron: str = "0 8 * * *"
    next_times: int = Field(default=3, ge=1, le=100)
    random_seconds: int = Field(default=0, ge=0, le=86400)
    message_ids: list[int] = Field(default_factory=list, max_length=100)
