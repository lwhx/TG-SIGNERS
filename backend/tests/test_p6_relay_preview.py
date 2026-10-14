"""P6 full-preview stays offline and matches the real relay payload builder."""


def config_for_preview():
    return {
        "rules": [{
            "id": "115relay",
            "enabled": True,
            "triggers": [{"type": "message", "params": {"chat_ids": ["@source"]}}],
            "filters": {"text_rule": "regex", "text_value": r"115cdn\.com/s/", "ignore_case": True},
            "handlers": [{"handler": "relay_message", "params": {
                "ignore_self": True,
                "targets": [
                    {"chat_id": "@115bot", "mode": "extract",
                     "pattern": r"(https?://115cdn\.com/s/[^\s]+)",
                     "group": 1, "dedup": "content"},
                    {"chat_id": "@copy", "mode": "template",
                     "template": "链接 {group1} 来源 {chat_id}",
                     "pattern": r"(https?://115cdn\.com/s/[^\s]+)",
                     "dedup": "message"},
                    {"chat_id": "@mirror", "mode": "forward", "dedup": "message"},
                    {"chat_id": "@copymedia", "mode": "copy", "dedup": "message"},
                ],
            }}],
            "vars": {},
        }]
    }


def test_full_preview_offline_all_modes_and_filter(admin):
    body = {
        "config": config_for_preview(),
        "source_chat": "@source",
        "source_message": 9,
        "sample": "看这里 https://115cdn.com/s/abc123",
    }
    response = admin.post("/api/relay/full-preview", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["potential_messages"] == 4
    rule = data["rules"][0]
    assert rule["matched"] is True
    targets = rule["targets"]
    assert [t["mode"] for t in targets] == ["extract", "template", "forward", "copy"]
    assert targets[0]["payloads"] == ["https://115cdn.com/s/abc123"]
    assert targets[1]["payloads"] == ["链接 https://115cdn.com/s/abc123 来源 @source"]
    assert targets[2]["payloads"] == []
    assert targets[3]["payloads"] == []
    assert all(t["would_send"] for t in targets)
    assert "不发送 Telegram" in data["warning"]

    unmatched = admin.post("/api/relay/full-preview", json={
        **body, "source_chat": "@another"
    })
    assert unmatched.status_code == 200
    assert unmatched.json()["potential_messages"] == 0
    assert unmatched.json()["rules"][0]["matched"] is False
    ignored = admin.post("/api/relay/full-preview", json={
        **body, "is_self": True
    })
    assert ignored.status_code == 200
    assert ignored.json()["potential_messages"] == 0
    assert all(not row["would_send"] for row in ignored.json()["rules"][0]["targets"])


def test_preview_validation_and_authentication(client, admin):
    body = {
        "config": config_for_preview(), "source_chat": "@source",
        "sample": "https://115cdn.com/s/ok",
    }
    assert client.post("/api/relay/full-preview", json=body,
                       headers={"Cookie": "tg_dashboard=invalid"}).status_code == 401
    assert admin.post("/api/relay/full-preview", json={
        **body, "source_chat": " ",
    }).status_code == 422
    assert admin.post("/api/relay/full-preview", json={
        **body, "sample": "x" * 8001,
    }).status_code == 422
    invalid = config_for_preview()
    invalid["rules"][0]["handlers"][0]["params"]["targets"][0]["chat_id"] = "@source"
    assert admin.post("/api/relay/full-preview", json={
        **body, "config": invalid,
    }).status_code == 422
