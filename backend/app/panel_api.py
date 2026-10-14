"""P8 panel/session endpoints split out from main to keep startup and auth isolated."""

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from tg_signer import __version__ as upstream_version

from .db import DATA, Session, get_setting, set_setting
from .schemas import PanelLogin, PasswordChange
from .security import COOKIE, SECURE, TTL, passwords, rate_limit, token

public_router = APIRouter()
private_router = APIRouter()


@public_router.get("/api/health", tags=["系统"], summary="容器健康检查")
def health():
    with Session() as db:
        db.execute(select(1))
    return {"status": "ok", "version": "1.1.0", "tg_signer": upstream_version}


@public_router.post("/api/panel/login", tags=["面板鉴权"], summary="密码登录，签发 HttpOnly JWT Cookie")
def panel_login(data: PanelLogin, request: Request, response: Response):
    rate_limit("panel:" + (request.client.host if request.client else "local"))
    if not passwords.verify(data.password, get_setting("password_hash")):
        raise HTTPException(401, "管理密码错误")
    jwt = token()
    response.set_cookie(COOKIE, jwt, max_age=TTL, httponly=True, secure=SECURE, samesite="strict")
    return {"status": "authenticated", "expires_in": TTL}


@private_router.get("/panel/me", tags=["面板鉴权"])
def panel_me():
    return {"user": "admin"}


@private_router.post("/panel/logout", tags=["面板鉴权"])
def panel_logout(response: Response):
    response.delete_cookie(COOKIE, secure=SECURE, samesite="strict")
    return {"status": "ok"}


@private_router.put("/panel/password", tags=["面板鉴权"], summary="修改密码并使旧 JWT 失效")
def password_change(data: PasswordChange, response: Response):
    rate_limit("password-change", 5, 300)
    if not passwords.verify(data.current_password, get_setting("password_hash")):
        raise HTTPException(400, "当前密码错误")
    set_setting("password_hash", passwords.hash(data.new_password))
    set_setting("token_version", secrets.token_hex(16))
    response.set_cookie(
        COOKIE, token(), max_age=TTL, httponly=True, secure=SECURE, samesite="strict"
    )
    (DATA / "initial-password.txt").unlink(missing_ok=True)
    return {"status": "ok"}
