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
