"""FastAPI license verification server.

Usage:
    uvicorn license_server.server:app --host 0.0.0.0 --port 8900

Environment variables:
    LICENSE_ADMIN_KEY  — required admin API key (set before starting)
"""

import os
import logging

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from license_server.database import (
    init_db, activate_key, check_key,
    generate_keys, list_keys, ban_key, unban_key, delete_key, get_stats,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_KEY = os.environ.get("LICENSE_ADMIN_KEY", "")

app = FastAPI(title="TG Bot License Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ──────────────────────────────────────────────

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


# ── Admin key dependency ─────────────────────────────────────────

def verify_admin(request: Request):
    token = request.headers.get("X-Admin-Key", "")
    if not ADMIN_KEY:
        raise HTTPException(500, "服务端未配置 LICENSE_ADMIN_KEY")
    if token != ADMIN_KEY:
        raise HTTPException(403, "管理密钥无效")


# ── Startup ──────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup():
    init_db()
    if not ADMIN_KEY:
        logger.warning("LICENSE_ADMIN_KEY not set — admin endpoints will reject all requests")
    logger.info("License server started")


# ── Public endpoints ─────────────────────────────────────────────

@app.post("/api/activate")
async def api_activate(body: ActivateRequest, request: Request):
    ip = request.client.host if request.client else ""
    result = activate_key(body.key, body.hwid, ip)
    return result


@app.post("/api/check")
async def api_check(body: CheckRequest, request: Request):
    ip = request.client.host if request.client else ""
    result = check_key(body.key, body.hwid, ip)
    return result


@app.get("/api/ping")
async def api_ping():
    return {"status": "ok"}


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
