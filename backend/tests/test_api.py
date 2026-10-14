from backend.app.db import Account, Session
from backend.app.security import decrypt


def task_payload(account_id, **overrides):
    return {
        "name": "daily sign",
        "account_id": account_id,
        "enabled": False,
        "delay_min": 10,
        "delay_max": 300,
        "config": {
            "sign_at": "0 8 * * *",
            "chats": [
                {
                    "chat_id": "@TestBot",
                    "actions": [{"action": 1, "text": "/checkin"}, {"action": 3, "text": "签到"}],
                }
            ],
        },
        **overrides,
    }


def test_authentication_and_csrf(client):
    client.cookies.clear()
    assert client.get("/api/accounts").status_code == 401
    assert client.post("/api/panel/login", json={"password": "bad"}).status_code == 401
    assert (
        client.post(
            "/api/panel/login",
            json={"password": "test-password-long-enough"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    response = client.post("/api/panel/login", json={"password": "test-password-long-enough"})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def test_account_secrets_never_leak(admin, account):
    result = admin.get("/api/accounts").json()[0]
    assert not {"session", "api_hash", "api_id"} & result.keys()
    with Session() as db:
        a = db.get(Account, account)
        assert a.session != "test-session"
        assert decrypt(a.session) == "test-session"
    assert admin.get(f"/api/accounts/{account}/export").text == "test-session"


def test_task_crud_validation_and_export(admin, account):
    payload = task_payload(account)
    response = admin.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    assert (
        admin.get(f"/api/tasks/{task_id}").json()["config"]["chats"][0]["actions"][1]["action"] == 3
    )
    invalid = {**payload, "delay_min": 400}
    assert admin.put(f"/api/tasks/{task_id}", json=invalid).status_code == 422
    assert (
        admin.post("/api/tasks/validate", json={**payload, "success_pattern": "["}).status_code
        == 422
    )
    assert (
        admin.post("/api/tasks/validate", json={**payload, "cron": "nonsense"}).status_code == 422
    )
    assert (
        admin.post("/api/tasks/validate", json={**payload, "timezone": "Fake/Zone"}).status_code
        == 422
    )
    exported = admin.get(f"/api/tasks/{task_id}/export?format=yaml")
    assert exported.status_code == 200
    assert "chat_id:" in exported.text
    assert "application" not in exported.headers["content-type"]
    assert admin.delete(f"/api/accounts/{account}").status_code == 409
    assert (
        admin.put(f"/api/tasks/{task_id}", json={**payload, "name": "renamed"}).json()["name"]
        == "renamed"
    )
    assert admin.delete(f"/api/tasks/{task_id}").status_code == 200
    assert admin.get(f"/api/tasks/{task_id}").status_code == 404


def test_settings_secret_mask_and_notification_validation(admin):
    data = {
        "global_proxy": "socks5://user:secret@127.0.0.1:1080",
        "ai_key": "sk-test-only",
        "ai_model": "test-model",
        "notifications": [
            {
                "type": "webhook",
                "url": "https://example.test",
                "headers": {"Authorization": "secret-value"},
            }
        ],
    }
    response = admin.put("/api/settings", json=data)
    assert response.status_code == 200
    assert "sk-test-only" not in response.text
    assert "secret-value" not in response.text
    assert "user:secret" not in response.text
    result = response.json()
    assert admin.put("/api/settings", json=result).status_code == 200
    assert (
        admin.put(
            "/api/settings", json={**result, "notifications": [{"type": "telegram"}]}
        ).status_code
        == 422
    )
    assert admin.put("/api/settings", json={"global_proxy": "ftp://wrong"}).status_code == 400
    admin.put("/api/settings", json={})


def test_config_yaml_and_automation_schema(admin, account):
    config = b"rules:\n  - id: startup\n    triggers:\n      - type: startup\n    handlers:\n      - handler: delay\n        params: {seconds: 1}\n"
    parsed = admin.post(
        "/api/config/parse", files={"file": ("test.yaml", config, "application/yaml")}
    )
    assert parsed.status_code == 200
    payload = task_payload(account, kind="automation", config=parsed.json())
    assert admin.post("/api/tasks/validate", json=payload).status_code == 200
    caps = admin.get("/api/capabilities").json()
    assert {"ai_reply", "forward", "schedule_next", "store_state"} <= set(caps["handlers"])


def test_import_rejects_bad_files_without_network(admin):
    response = admin.post("/api/accounts/import", files={"file": ("bad.session", b"not sqlite")})
    assert response.status_code == 400
    response = admin.post("/api/accounts/import", data={"session_string": "short"})
    assert response.status_code == 400


def test_logs_pagination_and_overview(admin):
    assert admin.get("/api/logs?size=200").status_code == 422
    assert admin.get("/api/logs?page=1&status=success&q=%25").json()["total"] == 0
    overview = admin.get("/api/overview").json()
    assert len(overview["trend"]) == 7
    assert overview["success_rate"] is None
    assert admin.get("/api/openapi.json").json()["info"]["title"] == "tg-signer Dashboard API"


def test_websocket_authenticated_history(admin):
    with admin.websocket_connect("/ws/logs", headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_json()["type"] == "history"
        # Send a close frame explicitly before TestClient tears down the
        # transport; otherwise Starlette 1.6 can race and cancel its portal.
        ws.close(code=1000)


def test_legacy_v1_import_and_timezone_order(admin, account):
    result = admin.post(
        "/api/tasks/validate",
        json=task_payload(
            account,
            config={
                "chat_id": 123,
                "sign_text": "/sign",
                "sign_at": "08:00:00",
                "random_seconds": 30,
            },
        ),
    )
    assert result.status_code == 200, result.text
    assert result.json()["config"]["chats"][0]["actions"][0]["text"] == "/sign"
    from backend.app.db import Task

    with Session() as db:
        for name, value in [
            ("later", "2099-01-01T08:00:00+00:00"),
            ("earlier", "2099-01-01T09:00:00+08:00"),
        ]:
            db.add(Task(name=name, account_id=account, enabled=True, next_run=value, config={}))
        db.commit()
    assert admin.get("/api/overview").json()["next_run"] == "2099-01-01T09:00:00+08:00"
