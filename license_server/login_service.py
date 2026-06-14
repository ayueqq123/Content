"""Server-side Telethon login engine.

Manages Telethon client instances on the server so that
the desktop client never touches Telethon directly.
Sessions are generated server-side and can be downloaded
by authenticated clients.
"""

import os
import asyncio
import secrets
import time
import logging
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    PhoneNumberInvalidError,
    PasswordHashInvalidError,
    ApiIdInvalidError,
)

logger = logging.getLogger(__name__)

_SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
os.makedirs(_SESSIONS_DIR, exist_ok=True)

# session_id -> {client, phone, phone_code_hash, created_at, api_id, api_hash}
_active_sessions: dict[str, dict] = {}

# Auto-cleanup sessions older than 10 minutes
_SESSION_TTL = 600


def _parse_proxy(proxy_str: str) -> Optional[tuple]:
    if not proxy_str:
        return None
    if "://" in proxy_str:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_str)
        scheme = parsed.scheme.lower()
        if scheme in ("socks5", "socks4"):
            import socks
            proxy_type = socks.SOCKS5 if scheme == "socks5" else socks.SOCKS4
            return (proxy_type, parsed.hostname, parsed.port, True,
                    parsed.username, parsed.password)
        elif scheme in ("http", "https"):
            import socks
            return (socks.HTTP, parsed.hostname, parsed.port, True,
                    parsed.username, parsed.password)
        return None
    parts = proxy_str.split(":")
    if len(parts) < 2:
        return None
    import socks
    host = parts[0]
    try:
        port = int(parts[1])
    except ValueError:
        return None
    username = parts[2] if len(parts) > 2 else None
    password = parts[3] if len(parts) > 3 else None
    return (socks.SOCKS5, host, port, True, username, password)


def _session_path(session_id: str, phone: str) -> str:
    safe_phone = phone.replace("+", "").replace(" ", "")
    return os.path.join(_SESSIONS_DIR, f"{session_id}_{safe_phone}")


async def cleanup_expired():
    """Remove sessions older than TTL."""
    now = time.time()
    expired = [
        sid for sid, s in _active_sessions.items()
        if now - s["created_at"] > _SESSION_TTL
    ]
    for sid in expired:
        await cancel_session(sid)


async def send_code(
    phone: str,
    api_id: int,
    api_hash: str,
    proxy: str = "",
    device_model: str = "Desktop Client",
) -> dict:
    """Create a Telethon client and send verification code."""
    await cleanup_expired()

    if api_id <= 0 or not api_hash:
        return {"success": False, "message": "API_ID/API_HASH 未配置"}

    session_id = secrets.token_hex(16)
    sess_path = _session_path(session_id, phone)
    proxy_tuple = _parse_proxy(proxy)

    client = TelegramClient(
        sess_path,
        api_id=api_id,
        api_hash=api_hash,
        proxy=proxy_tuple,
        device_model=device_model or "Desktop Client",
    )

    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        _active_sessions[session_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "created_at": time.time(),
            "api_id": api_id,
            "api_hash": api_hash,
        }
        logger.info(f"Code sent for {phone}, session_id={session_id[:8]}...")
        return {
            "success": True,
            "session_id": session_id,
            "message": f"验证码已发送到 {phone}",
        }
    except PhoneNumberInvalidError:
        await client.disconnect()
        _cleanup_session_files(sess_path)
        return {"success": False, "message": "手机号格式无效"}
    except ApiIdInvalidError:
        await client.disconnect()
        _cleanup_session_files(sess_path)
        return {"success": False, "message": "API_ID/API_HASH 组合无效"}
    except FloodWaitError as e:
        await client.disconnect()
        _cleanup_session_files(sess_path)
        return {"success": False, "message": f"请求过于频繁，请等待 {e.seconds} 秒"}
    except Exception as e:
        logger.exception(f"send_code failed for {phone}")
        await client.disconnect()
        _cleanup_session_files(sess_path)
        return {"success": False, "message": f"发送验证码失败: {e}"}


async def verify_code(session_id: str, phone: str, code: str) -> dict:
    """Verify the code and sign in."""
    session = _active_sessions.get(session_id)
    if not session:
        return {"success": False, "message": "会话不存在或已过期"}
    if session["phone"] != phone:
        return {"success": False, "message": "手机号不匹配"}

    client: TelegramClient = session["client"]
    phone_code_hash = session["phone_code_hash"]

    try:
        user = await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        info = {
            "user_id": user.id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
        }
        await client.disconnect()
        _active_sessions.pop(session_id, None)
        logger.info(f"Login success for {phone}, user_id={user.id}")
        return {
            "success": True,
            "message": "登录成功",
            "user_info": info,
            "session_id": session_id,
        }
    except SessionPasswordNeededError:
        return {
            "success": False,
            "need_2fa": True,
            "message": "此账号开启了两步验证",
        }
    except PhoneCodeInvalidError:
        return {"success": False, "message": "验证码错误"}
    except PhoneCodeExpiredError:
        await cancel_session(session_id)
        return {"success": False, "message": "验证码已过期"}
    except Exception as e:
        return {"success": False, "message": f"登录失败: {e}"}


async def verify_2fa(session_id: str, phone: str, password: str) -> dict:
    """Complete 2FA sign-in."""
    session = _active_sessions.get(session_id)
    if not session:
        return {"success": False, "message": "会话不存在或已过期"}
    if session["phone"] != phone:
        return {"success": False, "message": "手机号不匹配"}

    client: TelegramClient = session["client"]

    try:
        user = await client.sign_in(password=password)
        info = {
            "user_id": user.id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
        }
        await client.disconnect()
        _active_sessions.pop(session_id, None)
        logger.info(f"2FA login success for {phone}, user_id={user.id}")
        return {
            "success": True,
            "message": "两步验证通过，登录成功",
            "user_info": info,
            "session_id": session_id,
        }
    except PasswordHashInvalidError:
        return {"success": False, "message": "两步验证密码错误"}
    except Exception as e:
        return {"success": False, "message": f"两步验证失败: {e}"}


async def check_session_status(session_id: str, phone: str) -> dict:
    """Check if a completed session is still valid."""
    sess_path = _session_path(session_id, phone) + ".session"
    if not os.path.exists(sess_path):
        return {"success": False, "message": "Session 文件不存在"}

    session = _active_sessions.get(session_id)
    if session:
        api_id = session["api_id"]
        api_hash = session["api_hash"]
    else:
        return {"success": False, "message": "会话信息已过期，无法检查"}

    client = TelegramClient(
        _session_path(session_id, phone),
        api_id=api_id,
        api_hash=api_hash,
    )
    try:
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            await client.disconnect()
            return {
                "success": True,
                "message": "账号在线",
                "user_info": {
                    "user_id": me.id,
                    "first_name": me.first_name or "",
                    "last_name": me.last_name or "",
                    "username": me.username or "",
                },
            }
        await client.disconnect()
        return {"success": False, "message": "Session 已失效"}
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        return {"success": False, "message": f"检查失败: {e}"}


def get_session_file_path(session_id: str, phone: str) -> Optional[str]:
    """Return the path to the session file if it exists."""
    sess_path = _session_path(session_id, phone) + ".session"
    if os.path.exists(sess_path):
        return sess_path
    return None


async def cancel_session(session_id: str):
    """Cancel a pending login session and clean up."""
    session = _active_sessions.pop(session_id, None)
    if session:
        client = session.get("client")
        phone = session.get("phone", "")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(0.1)
        _cleanup_session_files(_session_path(session_id, phone))


def _cleanup_session_files(base_path: str):
    """Remove session files from disk."""
    suffixes = [".session", ".session-journal", ".session-wal", ".session-shm"]
    for suffix in suffixes:
        path = base_path + suffix
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
