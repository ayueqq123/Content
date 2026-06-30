"""FastAPI license verification server + admin Web UI.

Usage:
    uvicorn license_server.server:app --host 0.0.0.0 --port 8900

Environment variables:
    LICENSE_ADMIN_KEY       — admin API key for programmatic access (curl etc.)
    LICENSE_ADMIN_PASSWORD  — admin panel login password (fallback)
    DAOHAO_DB_PATH          — path to daohao's sqlite DB; if set, the admin panel
                              login reuses daohao's `web_password` setting
"""

import os
import time
import secrets
import sqlite3
import logging

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from license_server.database import (
    init_db, activate_key, check_key,
    generate_keys, list_keys, ban_key, unban_key, delete_key, get_stats,
)
from license_server import login_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

ADMIN_KEY = os.environ.get("LICENSE_ADMIN_KEY", "")
ADMIN_PASSWORD_ENV = os.environ.get("LICENSE_ADMIN_PASSWORD", "")
DAOHAO_DB_PATH = os.environ.get("DAOHAO_DB_PATH", "")

app = FastAPI(title="TG Bot License Server", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session + lockout stores (reused from daohao auth pattern)
_active_sessions: set[str] = set()
_failed_attempts: dict[str, dict] = {}


# ── Pydantic models ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str

class ActivateRequest(BaseModel):
    key: str
    hwid: str

class CheckRequest(BaseModel):
    key: str
    hwid: str

class GenerateRequest(BaseModel):
    count: int = 1
    days: int = 30
    prefix: str = "TG"
    note: str = ""

class KeyActionRequest(BaseModel):
    key: str


class RemoteSendCodeRequest(BaseModel):
    phone: str
    api_id: int
    api_hash: str
    proxy: str = ""
    device_model: str = "Desktop Client"
    license_key: str
    hwid: str


class RemoteVerifyCodeRequest(BaseModel):
    session_id: str
    phone: str
    code: str
    license_key: str = ""


class RemoteVerify2FARequest(BaseModel):
    session_id: str
    phone: str
    password: str
    license_key: str = ""


class RemoteSessionRequest(BaseModel):
    session_id: str
    phone: str
    license_key: str = ""


class CheckSessionRequest(BaseModel):
    phone: str
    api_id: int
    api_hash: str
    proxy: str = ""
    license_key: str = ""
    hwid: str = ""


# ── Auth helpers (reuse daohao login) ────────────────────────────

def get_admin_password() -> str:
    """Resolve the admin panel password.

    Priority: daohao DB `web_password` → LICENSE_ADMIN_PASSWORD → LICENSE_ADMIN_KEY
    """
    if DAOHAO_DB_PATH and os.path.exists(DAOHAO_DB_PATH):
        try:
            conn = sqlite3.connect(DAOHAO_DB_PATH)
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'web_password'"
            ).fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
        except Exception as e:
            logger.error(f"Failed to read daohao password: {e}")
    if ADMIN_PASSWORD_ENV:
        return ADMIN_PASSWORD_ENV
    return ADMIN_KEY


def verify_admin(request: Request):
    """Allow either a logged-in session token (Bearer) or the X-Admin-Key header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        if token in _active_sessions:
            return True

    key = request.headers.get("X-Admin-Key", "")
    if ADMIN_KEY and key == ADMIN_KEY:
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未授权：请先登录或提供有效的管理密钥",
    )


# ── Startup ──────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup():
    init_db()
    if not get_admin_password():
        logger.warning("No admin password/key configured — admin panel will reject all logins")
    logger.info("License server started")


# ── Admin Web UI ─────────────────────────────────────────────────

@app.get("/")
async def admin_panel():
    index = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    raise HTTPException(404, "admin panel not found")


@app.post("/api/auth/login")
async def api_login(req: LoginRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()

    if ip in _failed_attempts:
        record = _failed_attempts[ip]
        if record["lockout_until"] > now:
            remaining = int(record["lockout_until"] - now)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"密码错误次数过多，该 IP 已被临时锁定。请在 {remaining} 秒后再试。",
            )

    expected = get_admin_password()
    if expected and secrets.compare_digest(req.password, expected):
        _failed_attempts.pop(ip, None)
        token = secrets.token_hex(32)
        _active_sessions.add(token)
        return {"success": True, "token": token}

    if ip not in _failed_attempts:
        _failed_attempts[ip] = {"count": 1, "lockout_until": 0}
    else:
        _failed_attempts[ip]["count"] += 1

    if _failed_attempts[ip]["count"] >= 5:
        _failed_attempts[ip]["lockout_until"] = now + 900
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="密码错误次数过多，该 IP 已被锁定 15 分钟。",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"密码错误。您还有 {5 - _failed_attempts[ip]['count']} 次尝试机会。",
    )


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        _active_sessions.discard(auth_header.split(" ", 1)[1])
    return {"success": True}


# ── Public endpoints ─────────────────────────────────────────────

@app.post("/api/activate")
async def api_activate(body: ActivateRequest, request: Request):
    ip = request.client.host if request.client else ""
    return activate_key(body.key, body.hwid, ip)


@app.post("/api/check")
async def api_check(body: CheckRequest, request: Request):
    ip = request.client.host if request.client else ""
    return check_key(body.key, body.hwid, ip)


@app.get("/api/ping")
async def api_ping():
    return {"status": "ok"}


# ── Remote login endpoints (license-gated) ───────────────────────

def _verify_license(key: str, hwid: str) -> bool:
    """Check that the caller has a valid license before allowing login."""
    result = check_key(key, hwid)
    return result.get("valid", False)


@app.post("/api/login/send_code")
async def api_login_send_code(body: RemoteSendCodeRequest):
    if not _verify_license(body.license_key, body.hwid):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "卡密无效或已过期")
    return await login_service.send_code(
        phone=body.phone,
        api_id=body.api_id,
        api_hash=body.api_hash,
        proxy=body.proxy,
        device_model=body.device_model,
        license_key=body.license_key,
    )


@app.post("/api/login/verify_code")
async def api_login_verify_code(body: RemoteVerifyCodeRequest):
    return await login_service.verify_code(body.session_id, body.phone, body.code)


@app.post("/api/login/verify_2fa")
async def api_login_verify_2fa(body: RemoteVerify2FARequest):
    return await login_service.verify_2fa(body.session_id, body.phone, body.password)


@app.post("/api/login/download_session")
async def api_login_download_session(body: RemoteSessionRequest):
    path = login_service.get_session_file_path(body.session_id, body.phone, body.license_key)
    if not path:
        raise HTTPException(404, "Session 文件不存在")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=os.path.basename(path),
    )


@app.post("/api/login/download_tdata")
async def api_login_download_tdata(body: RemoteSessionRequest):
    zip_path = await login_service.get_tdata_zip_path(
        body.session_id, body.phone, body.license_key)
    if not zip_path:
        raise HTTPException(500, "tdata 转换失败或 session 不存在")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=os.path.basename(zip_path),
    )


@app.post("/api/login/check_session")
async def api_login_check_session(body: CheckSessionRequest):
    if body.license_key and body.hwid:
        if not _verify_license(body.license_key, body.hwid):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "卡密无效或已过期")
    return await login_service.check_session_valid(
        phone=body.phone,
        api_id=body.api_id,
        api_hash=body.api_hash,
        proxy=body.proxy,
        license_key=body.license_key,
    )


@app.post("/api/login/cancel")
async def api_login_cancel(body: RemoteSessionRequest):
    await login_service.cancel_session(body.session_id)
    return {"success": True}


# ── Admin endpoints ──────────────────────────────────────────────

@app.post("/api/admin/generate", dependencies=[Depends(verify_admin)])
async def api_generate(body: GenerateRequest):
    if body.count < 1 or body.count > 500:
        raise HTTPException(400, "count 范围: 1-500")
    if body.days < 1:
        raise HTTPException(400, "days 必须 >= 1")
    keys = generate_keys(body.count, body.days, body.prefix, body.note)
    return {"keys": keys, "count": len(keys)}


@app.get("/api/admin/list", dependencies=[Depends(verify_admin)])
async def api_list(status: str = "", page: int = 1, page_size: int = 50):
    return list_keys(status, page, page_size)


@app.post("/api/admin/ban", dependencies=[Depends(verify_admin)])
async def api_ban(body: KeyActionRequest):
    return ban_key(body.key)


@app.post("/api/admin/unban", dependencies=[Depends(verify_admin)])
async def api_unban(body: KeyActionRequest):
    return unban_key(body.key)


@app.post("/api/admin/delete", dependencies=[Depends(verify_admin)])
async def api_delete(body: KeyActionRequest):
    return delete_key(body.key)


@app.get("/api/admin/stats", dependencies=[Depends(verify_admin)])
async def api_stats():
    return get_stats()
