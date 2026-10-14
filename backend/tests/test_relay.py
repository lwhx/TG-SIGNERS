"""Regressions for the upgraded generic relay engine in the original dashboard."""

import pytest
from fastapi.testclient import TestClient

from backend.app.relay import (
    RelayPreviewInput,
    preview_match,
    validate_relay_config,
)


def _relay_target(**changes):
    return {"chat_id": "@target", "mode": "extract", "pattern": r"(https?://[^\s]+)", "group": 1, "dedup": "content"} | changes


def _relay_config(target=None):
    return {
        "rules": [{
            "id": "relay_links",
            "triggers": [{"type": "message", "params": {"chat_ids": ["@source_a", "@source_b"]}}],
            "handlers": [{"handler": "relay_message", "params": {
                "ignore_self": True, "targets": [target or _relay_target()],
            }}],
        }]
    }


def test_year_is_dynamic_and_short_duration_is_excluded():
    pattern = (
        r"\A20\d{2}｜长时｜连载｜国漫｜[^\r\n]+"
        r"\n(https?://115cdn\.com/s/[^\s]+)\Z"
    )
    long_msg = (
        "2027｜长时｜连载｜国漫｜22T\n"
        "https://115cdn.com/s/sws8a233w30?password=bf64#"
    )
    hit = preview_match(RelayPreviewInput(pattern=pattern, sample=long_msg))
    assert hit["matched"]
    assert hit["groups"] == ["https://115cdn.com/s/sws8a233w30?password=bf64#"]
    assert not preview_match(
        RelayPreviewInput(pattern=pattern, sample=long_msg.replace("长时", "短时"))
    )["matched"]


def test_generic_relay_validation():
    validate_relay_config(_relay_config())
    validate_relay_config(_relay_config({"chat_id": "me", "mode": "copy", "dedup": "message"}))
    with pytest.raises(ValueError, match="来源相同"):
        validate_relay_config(_relay_config(_relay_target(chat_id="@source_a")))
    with pytest.raises(ValueError, match="捕获组"):
        validate_relay_config(_relay_config(_relay_target(group=2)))
    with pytest.raises(ValueError, match="提取正则"):
        validate_relay_config(_relay_config(_relay_target(pattern="")))
    with pytest.raises(ValueError, match="去重策略"):
        validate_relay_config(_relay_config(_relay_target(dedup="bad")))


def test_preview_limits_and_match_modes():
    assert preview_match(
        RelayPreviewInput(text_rule="contains", pattern="NEWS", sample="some news")
    )["matched"]
    assert not preview_match(
        RelayPreviewInput(text_rule="contains", pattern="xyz", sample="news")
    )["matched"]
    assert preview_match(
        RelayPreviewInput(text_rule="exact", pattern="hello", sample="HELLO")
    )["matched"]
    with pytest.raises(ValueError, match="正则测试失败"):
        preview_match(RelayPreviewInput(pattern="(", sample="hello"))


def test_authenticated_regex_preview_endpoint(admin: TestClient):
    response = admin.post("/api/relay/preview", json={
        "text_rule": "regex", "pattern": r"(?P<code>\d+)",
        "sample": "验证码 12345",
    })
    assert response.status_code == 200
    assert response.json()["named_groups"] == {"code": "12345"}
    bad = admin.post("/api/relay/preview", json={
        "text_rule": "regex", "pattern": "(", "sample": "abc"
    })
    assert bad.status_code == 422
