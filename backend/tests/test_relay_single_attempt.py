"""Only relay sends disable SDK retries; other Telegram operations keep defaults."""

import pytest

from backend.app.telegram import HeadlessClient


@pytest.mark.asyncio
async def test_relay_context_limits_sdk_attempts(monkeypatch):
    from tg_signer import core
    from tg_signer.automation.delivery_outcome import relay_attempt

    seen = []

    async def invoke(self, query, *args, **kwargs):
        seen.append(kwargs)
        return "ok"

    monkeypatch.setattr(core.Client, "invoke", invoke)
    client = object.__new__(HeadlessClient)
    await client.invoke("normal")
    token = relay_attempt.set(True)
    try:
        await client.invoke("relay")
    finally:
        relay_attempt.reset(token)
    assert seen[0] == {}
    assert seen[1]["retries"] == 1 and seen[1]["sleep_threshold"] == 0


@pytest.mark.asyncio
async def test_successful_send_rpc_is_recorded_before_high_level_parsing(monkeypatch):
    from types import SimpleNamespace

    from tg_signer import core
    from tg_signer.automation.delivery_outcome import relay_attempt, relay_rpc_state

    async def invoke(self, query, **kwargs):
        return object()

    monkeypatch.setattr(core.Client, "invoke", invoke)
    client = object.__new__(HeadlessClient)
    state = {"accepted": False}
    token = relay_attempt.set(True)
    rpc_token = relay_rpc_state.set(state)
    try:
        await client.invoke(SimpleNamespace(QUALNAME="functions.messages.GetMessages"))
        assert state["accepted"] is False
        await client.invoke(SimpleNamespace(QUALNAME="functions.messages.SendMessage"))
        assert state["accepted"] is True
    finally:
        relay_attempt.reset(token)
        relay_rpc_state.reset(rpc_token)
