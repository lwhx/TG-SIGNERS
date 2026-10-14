"""Opt-in bounded replay of confirmed temporary rejection; never ambiguous sends."""

import asyncio
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from .db import get_setting, set_setting
from .events import bus
from .relay_history import ReplayInput, get_ledger, replay_record

router = APIRouter(prefix="/relay", tags=["转发可靠性"])
_processing = asyncio.Lock()


class RetryPolicy(BaseModel):
    enabled: bool = False
    max_attempts: int = Field(3, ge=1, le=10)
    base_delay: int = Field(60, ge=30, le=3600)
    max_delay: int = Field(3600, ge=60, le=86400)
    horizon_hours: int = Field(24, ge=1, le=168)

    @model_validator(mode="after")
    def delay_order(self):
        if self.max_delay < self.base_delay:
            raise ValueError("最大间隔不能小于重试基础间隔")
        return self


def retry_policy():
    return RetryPolicy.model_validate_json(get_setting("relay_retry_policy", "{}"))


@router.get("/retry-policy")
def retry_settings():
    return {"policy": retry_policy().model_dump(), "batch_size": 5, "interval": 30}


@router.put("/retry-policy")
def save_retry_policy(config: RetryPolicy):
    set_setting("relay_retry_policy", config.model_dump_json())
    return retry_settings()


async def process_retries():
    if _processing.locked():
        return
    async with _processing:
        try:
            ledger = await asyncio.to_thread(get_ledger)
            await asyncio.to_thread(ledger.recover_stale)
            config = await asyncio.to_thread(retry_policy)
            await asyncio.to_thread(
                ledger.expire_retries,
                since=int(time.time()) - config.horizon_hours * 3600,
                max_attempts=config.max_attempts,
            )
            for _ in range(5):
                config = await asyncio.to_thread(retry_policy)
                if not config.enabled:
                    break
                claim = await asyncio.to_thread(
                    ledger.claim_retry,
                    max_attempts=config.max_attempts,
                    base_delay=config.base_delay,
                    since=int(time.time()) - config.horizon_hours * 3600,
                )
                if claim is None:
                    break
                reason = None
                try:
                    await replay_record(
                        ReplayInput(
                            destination=claim["destination"], fingerprint=claim["fingerprint"]
                        ),
                        claim=claim,
                    )
                except HTTPException as exc:
                    if exc.status_code in {400, 401, 403, 404, 409, 422}:
                        reason = str(exc.detail)[:200]
                except Exception as exc:
                    bus.emit(f"自动重试本次未完成：{type(exc).__name__}", "WARNING")
                finally:
                    await asyncio.to_thread(
                        ledger.settle_retry,
                        claim,
                        max_attempts=config.max_attempts,
                        base_delay=config.base_delay,
                        max_delay=config.max_delay,
                        stop_reason=reason,
                    )
        except Exception as exc:
            bus.emit(f"自动重试检查未完成：{type(exc).__name__}", "WARNING")
