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
    UnauthorizedError,
    AuthKeyDuplicatedError,
)

logger = logging.getLogger(__name__)

_SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
os.makedirs(_SESSIONS_DIR, exist_ok=True)

# session_id -> {client, phone, phone_code_hash, created_at, api_id, api_hash, license_key}
_active_sessions: dict[str, dict] = {}

# Auto-cleanup sessions older than 10 minutes
_SESSION_TTL = 600

# Per-session locks to prevent concurrent verify operations on the same session.
# Without this, two identical verify_code / verify_2fa requests arriving in the
# same event-loop tick can both read the session before either pops it, letting
# both return success and causing duplicate downstream notifications.
_session_locks: dict[str, asyncio.Lock] = {}


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


def _license_dir(license_key: str) -> str:
    """Return per-license-key subdirectory for session storage."""
    safe_key = license_key.replace("/", "_").replace("\\", "_")[:32]
    d = os.path.join(_SESSIONS_DIR, safe_key)
    os.makedirs(d, exist_ok=True)
    return d


def _session_path(session_id: str, phone: str, license_key: str = "") -> str:
    safe_phone = phone.replace("+", "").replace(" ", "")
    if license_key:
        return os.path.join(_license_dir(license_key), f"{session_id}_{safe_phone}")
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
    proxy_pool: Optional[list] = None,
    device_model: str = "Desktop Client",
    license_key: str = "",
) -> dict:
    """Create a Telethon client and send verification code."""
    await cleanup_expired()

    if api_id <= 0 or not api_hash:
        return {"success": False, "message": "API_ID/API_HASH 未配置"}

    session_id = secrets.token_hex(16)
    sess_path = _session_path(session_id, phone, license_key)

    # Ordered egress candidates: try the bound proxy first, then the rest of the
    # proxy pool one by one, and only fall back to a direct connection if EVERY
    # proxy fails to connect. A dead proxy must not block login outright — we
    # switch to the next proxy (self-heal), and if none work we degrade to
    # direct and tell the caller (via ``proxy_fell_back``) so it can warn the
    # group. The proxy actually used is persisted for later health checks.
    candidates: list[str] = []
    for cand in (proxy, *(proxy_pool or []), ""):
        if cand not in candidates:
            candidates.append(cand)

    client: Optional[TelegramClient] = None
    used_proxy: str = ""
    last_conn_err = None
    for cand in candidates:
        c = TelegramClient(
            sess_path,
            api_id=api_id,
            api_hash=api_hash,
            proxy=_parse_proxy(cand),
            device_model=device_model or "Desktop Client",
        )
        try:
            await c.connect()
        except Exception as e:
            # This egress could not reach Telegram (proxy down / network).
            # Try the next candidate before giving up.
            last_conn_err = e
            try:
                await c.disconnect()
            except Exception:
                pass
            continue
        client = c
        used_proxy = cand
        break

    if client is None:
        _cleanup_session_files(sess_path)
        return {"success": False,
                "message": f"无法连接 Telegram（代理与直连均失败）: {last_conn_err}"}

    # A proxy was requested but every pooled proxy failed and we ended up on a
    # direct connection. (Switching to another pool proxy is a silent self-heal
    # and is NOT reported as "fell back to direct".)
    proxy_fell_back = bool(proxy) and used_proxy == ""

    try:
        # Persist the proxy this session's auth was created through, so a later
        # health check can reuse the SAME egress (and fall back to direct if the
        # proxy dies) instead of relying on the client to re-send it.
        _save_session_proxy(sess_path, used_proxy)
        sent = await client.send_code_request(phone)
        _active_sessions[session_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "created_at": time.time(),
            "api_id": api_id,
            "api_hash": api_hash,
            "license_key": license_key,
        }
        logger.info(f"Code sent for {phone}, session_id={session_id[:8]}..."
                    + ("（代理失效，已退回直连）" if proxy_fell_back else ""))
        result = {
            "success": True,
            "session_id": session_id,
            "message": f"验证码已发送到 {phone}",
        }
        if proxy_fell_back:
            result["proxy_fell_back"] = True
            result["failed_proxy"] = proxy
        return result
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
    lock = _session_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
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
            _session_locks.pop(session_id, None)
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
            _active_sessions.pop(session_id, None)
            _session_locks.pop(session_id, None)
            # Clean up client outside lock to avoid nested-lock issues
            if session.get("client"):
                try:
                    await session["client"].disconnect()
                except Exception:
                    pass
            _cleanup_session_files(_session_path(
                session_id, phone, session.get("license_key", "")))
            return {"success": False, "message": "验证码已过期"}
        except Exception as e:
            return {"success": False, "message": f"登录失败: {e}"}


async def verify_2fa(session_id: str, phone: str, password: str) -> dict:
    """Complete 2FA sign-in."""
    lock = _session_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
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
            _session_locks.pop(session_id, None)
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


async def check_session_valid(phone: str, api_id: int, api_hash: str,
                              proxy: str = "", proxy_pool: Optional[list] = None,
                              license_key: str = "") -> dict:
    """Check if a session for the given phone is still authorized on Telegram.

    Finds the session file under the license_key directory and tries to connect.

    The check connects through the SAME ``proxy`` that was used to log the
    account in. Using a different exit IP than the one the authorization was
    created on makes Telegram reject the (otherwise valid) session, which would
    produce a false "session invalid" report. A connection/proxy failure is
    returned as ``inconclusive`` so callers do NOT treat it as an invalid
    session — only a confirmed unauthorized reply does.
    """
    import glob

    safe_phone = phone.replace("+", "").replace(" ", "")
    if license_key:
        search_dir = _license_dir(license_key)
    else:
        search_dir = _SESSIONS_DIR

    # Find session file: look for *_{phone}.session pattern
    pattern = os.path.join(search_dir, f"*_{safe_phone}.session")
    session_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not session_files:
        return {"success": False, "message": "Session 文件不存在"}

    # Use the most recent session file
    sess_file = session_files[0]
    base_path = sess_file[:-8]  # remove .session suffix

    # Build the ordered list of egresses to try, de-duplicated:
    #   1) proxy sent with this request (the phone's bound proxy)
    #   2) the proxy the session was originally logged in through (persisted)
    #   3) the rest of the proxy pool, one by one
    #   4) direct connection (no proxy) as a last-resort fallback
    # This way the check does not hard-depend on any single proxy: if the bound
    # /login proxy is dead we try the other pooled proxies before finally
    # falling back to direct, instead of falsely reporting the session invalid.
    # A proxy value of "" means direct.
    login_proxy = _load_session_proxy(base_path)
    candidates: list[str] = []
    for cand in (proxy, login_proxy, *(proxy_pool or []), ""):
        if cand not in candidates:
            candidates.append(cand)

    last_conn_err = None
    # Remember any non-empty proxy that failed to connect, so that when the
    # check ultimately succeeds over a direct connection we can report that the
    # proxy died and we fell back (``proxy_fell_back``) — the caller uses this
    # to warn the group.
    failed_proxy = ""
    for cand in candidates:
        client = TelegramClient(
            base_path, api_id=api_id, api_hash=api_hash,
            proxy=_parse_proxy(cand))
        try:
            try:
                await client.connect()
            except Exception as e:
                # Could not reach Telegram through this egress (proxy down,
                # network, DC unreachable). Try the next candidate before giving
                # up — a dead proxy must not look like an invalid session.
                last_conn_err = e
                if cand and not failed_proxy:
                    failed_proxy = cand
                continue
            # Probe with get_me() and classify the outcome by exception TYPE.
            # NOTE: do NOT use client.is_user_authorized() here — it swallows ANY
            # RPCError (e.g. transient FloodWait / server errors) and returns
            # False, which would falsely report a good session as invalid.
            try:
                me = await client.get_me()
            except (UnauthorizedError, AuthKeyDuplicatedError):
                # Telegram authoritatively says this authorization is gone
                # (auth key unregistered / session revoked / expired / banned /
                # duplicated). This is the ONLY case that means "session invalid".
                return {"success": False, "message": "Session 已失效（账号未授权）"}
            except FloodWaitError as e:
                return {
                    "success": False,
                    "inconclusive": True,
                    "message": f"验证被限流（FloodWait {e.seconds}s），未能确认",
                }
            except Exception as e:
                # Any other error (network reset, timeout, other RPC errors) says
                # nothing about the session — inconclusive, do NOT report invalid.
                return {
                    "success": False,
                    "inconclusive": True,
                    "message": f"验证 session 时异常，未能确认: {e}",
                }
            if me is None:
                # Telethon's get_me() catches UnauthorizedError internally and
                # returns None instead of raising, so the except-branch above is
                # never hit for a revoked session — a None result after a
                # SUCCESSFUL connect() means the authorization is gone. Confirm
                # with is_user_authorized() (the connection already succeeded, so
                # this reflects the real auth state, not a transient connect
                # failure) before declaring the session dead.
                try:
                    authorized = await client.is_user_authorized()
                except Exception:
                    authorized = None
                if authorized is False:
                    return {
                        "success": False,
                        "message": "Session 已失效（账号未授权）",
                    }
                # Genuinely ambiguous (is_user_authorized itself failed) — stay
                # inconclusive rather than risk a false "invalid" report.
                return {
                    "success": False,
                    "inconclusive": True,
                    "message": "验证返回空结果，未能确认",
                }
            result = {
                "success": True,
                "message": "Session 有效，账号在线",
            }
            # Succeeded over a direct connection after a proxy failed to connect
            # => proxy died, we fell back to direct. Flag it so the caller can
            # notify the group; the session itself is still valid.
            if cand == "" and failed_proxy:
                result["proxy_fell_back"] = True
                result["failed_proxy"] = failed_proxy
            return result
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    # Every egress failed to even connect — say nothing about the session.
    return {
        "success": False,
        "inconclusive": True,
        "message": f"无法连接 Telegram（代理与直连均失败），未能验证: {last_conn_err}",
    }


def get_session_file_path(session_id: str, phone: str, license_key: str = "") -> Optional[str]:
    """Return the path to the session file if it exists."""
    sess_path = _session_path(session_id, phone, license_key) + ".session"
    if os.path.exists(sess_path):
        return sess_path
    # Fallback: check old flat path for backward compatibility
    if license_key:
        old_path = _session_path(session_id, phone, "") + ".session"
        if os.path.exists(old_path):
            return old_path
    return None


async def get_tdata_zip_path(session_id: str, phone: str, license_key: str = "") -> Optional[str]:
    """Convert the session to tdata format and return the zip path."""
    import tempfile
    import shutil

    sess_path = get_session_file_path(session_id, phone, license_key)
    if not sess_path:
        return None

    try:
        from opentele.td import TDesktop
        from opentele.tl import TelegramClient as OTelegramClient
        from opentele.api import UseCurrentSession
    except ImportError:
        logger.error("opentele not installed, cannot convert to tdata")
        return None

    safe_phone = phone.replace("+", "").replace(" ", "")
    if license_key:
        out_dir = _license_dir(license_key)
    else:
        out_dir = _SESSIONS_DIR

    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(out_dir, f"{safe_phone}_{ts}_tdata.zip")
    temp_dir = tempfile.mkdtemp()

    try:
        tdata_dir = os.path.join(temp_dir, "tdata")
        os.makedirs(tdata_dir, exist_ok=True)

        base_session_path = sess_path
        if base_session_path.endswith(".session"):
            base_session_path = base_session_path[:-8]

        client = OTelegramClient(base_session_path)
        await client.connect()
        try:
            tdesk = await client.ToTDesktop(flag=UseCurrentSession)
            tdesk.SaveTData(tdata_dir)
        finally:
            await client.disconnect()

        zip_base = os.path.join(temp_dir, f"{safe_phone}_{ts}_tdata")
        zip_filepath = shutil.make_archive(zip_base, "zip", root_dir=temp_dir, base_dir="tdata")

        shutil.move(zip_filepath, zip_path)
        logger.info(f"tdata zip created: {zip_path}")
        return zip_path
    except Exception as e:
        logger.error(f"tdata conversion failed for {phone}: {e}")
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def cancel_session(session_id: str):
    """Cancel a pending login session and clean up."""
    _session_locks.pop(session_id, None)
    session = _active_sessions.pop(session_id, None)
    if session:
        client = session.get("client")
        phone = session.get("phone", "")
        license_key = session.get("license_key", "")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(0.1)
        _cleanup_session_files(_session_path(session_id, phone, license_key))


def _save_session_proxy(base_path: str, proxy: str):
    """Persist the proxy used to create a session next to its .session file."""
    try:
        with open(base_path + ".proxy", "w", encoding="utf-8") as f:
            f.write(proxy or "")
    except Exception:
        pass


def _load_session_proxy(base_path: str) -> str:
    """Read the persisted login proxy for a session, or "" if none."""
    try:
        with open(base_path + ".proxy", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _cleanup_session_files(base_path: str):
    """Remove session files from disk."""
    suffixes = [".session", ".session-journal", ".session-wal",
                ".session-shm", ".proxy"]
    for suffix in suffixes:
        path = base_path + suffix
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
