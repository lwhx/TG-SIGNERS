"""P7 target settings are explicit and validated before any new relay work."""

import pytest

from backend.app.relay import validate_relay_config


def config(target):
    return {"rules": [{
        "id": "p7",
        "enabled": True,
        "triggers": [{"type":"message","params":{"chat_ids":["@source"]}}],
        "handlers": [{
            "handler": "relay_message",
            "params": {"targets": [target], "ignore_self":True},
        }],
    }]}


def test_bot_receipt_requires_patterns_and_bounded_window():
    base = {"chat_id":"@115bot","mode":"extract",
            "pattern":r"(https?://115cdn\.com/s/[^\s]+)","group":1,
            "track_bot_result":True}
    with pytest.raises(ValueError, match="Bot 结果正则"):
        validate_relay_config(config(base))
    both = {**base,"bot_success_pattern":r"转存成功",
            "bot_failure_pattern":r"转存失败", "bot_timeout_minutes":120}
    validate_relay_config(config(both))
    with pytest.raises(ValueError, match="Bot 回复等待时间"):
        validate_relay_config(config({**both,"bot_timeout_minutes":0}))
    with pytest.raises(ValueError, match="正则错误"):
        validate_relay_config(config({**both,"bot_failure_pattern":"("}))


def test_album_mode_restricts_actions_and_defaults_unmodified():
    single = {"chat_id":"@archive","mode":"forward","dedup":"message"}
    validate_relay_config(config(single))
    validate_relay_config(config({**single,"album_mode":"album"}))
    validate_relay_config(config({**single,"mode":"copy","album_mode":"album"}))
    with pytest.raises(ValueError, match="媒体组整组发送"):
        validate_relay_config(config({
            "chat_id":"@archive","mode":"text","album_mode":"album",
        }))
    with pytest.raises(ValueError, match="媒体组模式"):
        validate_relay_config(config({**single,"album_mode":"invalid"}))


@pytest.mark.asyncio
async def test_bot_message_reply_id_fallback_is_correlated_without_body(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from tg_signer.automation.relay_ledger import RelayLedger

    from backend.app import relay_history
    from backend.app.engine import Automation

    ledger = RelayLedger(tmp_path)
    key = "7" * 64
    assert ledger.reserve("@115bot", key, "text", "-100", 10) == ("reserved", 1)
    ledger.finish("@115bot", key, 1, True, destination_message=55)
    assert ledger.track_bot_send(
        destination="@115bot", fingerprint=key, account_id=4, task_id=3,
        bot_chat=999, sent_message=55, success_pattern="转存成功",
        failure_pattern="转存失败", timeout_minutes=120,
    )
    monkeypatch.setattr(relay_history, "get_ledger", lambda: ledger)
    message = SimpleNamespace(
        id=66, chat=SimpleNamespace(id=999),
        from_user=SimpleNamespace(is_bot=True), outgoing=False,
        reply_to_message=None, reply_to_message_id=55,
        text="转存成功", caption=None,
    )
    await Automation._observe_bot_receipt(SimpleNamespace(account_id=4), message)
    assert ledger.bot_results()["items"][0]["status"] == "succeeded"
    assert ledger.get("@115bot", key)["status"] == "sent"
