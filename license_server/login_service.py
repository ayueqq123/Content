"""Server-side Telethon login engine.

Manages Telethon client instances on the server so that
the desktop client never touches Telethon directly.
Sessions are generated server-side and can be downloaded
by authenticated clients.
"""

import os
import re
import asyncio
import secrets
import time
import math
import logging
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    PhoneNumberInvalidError,
    PhonePasswordFloodError,
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
_SEND_CODE_COOLDOWN = 60

# Per-candidate connection tuning for the proxy-failover chain. Telethon's
# defaults (connection_retries=5, timeout=10) let a single dead proxy block for
# ~50s before the next candidate is tried; with a proxy pool that turned
# send_code / check_session into multi-minute stalls. We fail fast on each
# candidate and rely on the ordered list (bound proxy -> a few pool alternates
# -> direct) for resilience.
_CONNECT_TIMEOUT = 8   # seconds per connection attempt
_CONNECT_RETRIES = 1   # attempts per candidate before moving to the next
_MAX_API_ID = 2_147_483_647
# Telethon reuses ``connection_retries`` for reconnects that happen *inside* a
# request: a PhoneMigrateError makes send_code reconnect to the new DC, and
# ToTDesktop reconnects while exporting. There is no candidate chain to fall
# back on at that point, so a single 8s attempt turns a slow gateway into a
# failed login ("send_code failed: Connection to Telegram failed 1 time(s)") or
# a 500 from download_tdata, which is why some accounts only get a .session and
# no tdata. Retry those, but only on the login/tdata paths: they run a few dozen
# times an hour, unlike check_session, where extra attempts congest the gateway.
_LOGIN_CONNECT_RETRIES = 3
# Cap how many pooled alternates we actually try. This is a load limit, not just
# defense-in-depth: every attempt opens another connection through the same
# proxy gateway, and the gateway congests. Measured on production (~2000
# check_session/hour, one Oxylabs account) over 8-minute windows:
#
#   candidates=8, timeout=12  -> 3-4 attempts/request, 95% inconclusive
#   candidates=8, timeout=8   -> 3 attempts/request,   71% inconclusive
#   candidates=1, timeout=8   -> 2 attempts/request,    0% inconclusive
#
# A proxy handshake takes 0.2-1.2s when connections are made sequentially but
# p50 8s / p90 15s at concurrency 10, so raising the attempt count feeds a
# congestion collapse: slower connects hold more sockets open, which slows
# connects further. Keep the chain short and let the retry (still 122 connect
# timeouts in the passing window) absorb individual bad egresses.
_MAX_POOL_CANDIDATES = 1
# send_code is different: it runs a few dozen times an hour (vs ~2000
# check_session/hour), so walking the whole pool does not congest the gateway,
# and a user-facing login should exhaust every configured egress before
# failing. The budget, not this cap, bounds how many candidates are tried.
_SEND_CODE_MAX_POOL_CANDIDATES = 64

# The server budget must remain below the client's HTTP timeout so a bounded
# failure reaches the caller before the client disconnects. Clients that do not
# report a timeout are assumed to use the old hard-coded 30s and get a 22s
# fallback. Clients that report their timeout get an adaptive budget: a 185s
# client gets min(180, 185 - 5) = 180s, with 5s reserved for TLS handshakes,
# nginx forwarding, and response transmission. Applies to send_code and
# check_session.
_SEND_CODE_DEFAULT_BUDGET = 22
_SEND_CODE_MAX_BUDGET = 180
_SEND_CODE_CLIENT_SAFETY_MARGIN = 5
_SEND_CODE_MAX_REPORTED_TIMEOUT = 300
_SEND_CODE_DEFAULT_DIRECT_RESERVE = 6
_SEND_CODE_MAX_DIRECT_RESERVE = 12

# Portion of the budget reserved for the FINAL candidate (normally the direct
# connection). Without this, a couple of dead proxies consume the whole budget
# and the final candidate is never attempted. Only applied when the chain
# actually ends with a direct candidate.
_DIRECT_RESERVE = 6

# A proxy that repeatedly fails to connect is temporarily skipped for new
# requests. Keep the threshold high enough to avoid overreacting to a
# transient failure, and let old failures decay so a proxy is not permanently
# penalized during a long-running process.
_PROXY_FAILURE_THRESHOLD = 5
_PROXY_COOLDOWN = 120
_PROXY_FAILURE_DECAY_WINDOW = 300
_proxy_health: dict[str, dict[str, float | int]] = {}

# The desktop client gives up after 60s while downloading tdata. Keep the
# complete conversion, including the Telegram connection and archive creation,
# below that deadline so the API can return a useful HTTP 500 instead of
# continuing after nginx has recorded a client disconnect.
_TDATA_TOTAL_TIMEOUT = 45

# Some hosts can only reach Telegram's data centers over IPv6 (their IPv4 route
# to the DC ranges is blocked), which makes every direct connection attempt time
# out. Setting LICENSE_TELEGRAM_USE_IPV6=1 makes direct connections resolve the
# DCs over IPv6. Proxied connections keep using IPv4 — the proxy provides egress.
_USE_IPV6 = os.getenv("LICENSE_TELEGRAM_USE_IPV6", "").strip().lower() in (
    "1", "true", "yes", "on",
)

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


def _make_client(session: str, api_id: int, api_hash: str, cand: str,
                 device_model: Optional[str] = None,
                 retries: int = _CONNECT_RETRIES) -> "TelegramClient":
    """Build a Telethon client for one egress candidate that fails fast.

    A short per-attempt timeout and a single connection attempt keep a dead
    proxy from stalling the whole failover chain.
    """
    proxy = _parse_proxy(cand)
    kwargs = dict(
        api_id=api_id,
        api_hash=api_hash,
        proxy=proxy,
        timeout=_CONNECT_TIMEOUT,
        connection_retries=retries,
        retry_delay=0,
        use_ipv6=_USE_IPV6 and proxy is None,
    )
    if device_model:
        kwargs["device_model"] = device_model
    client = TelegramClient(session, **kwargs)
    if kwargs["use_ipv6"]:
        _remap_session_dc_to_ipv6(client)
    elif proxy is not None:
        _remap_session_dc_to_ipv4(client)
    return client


# Telegram's per-DC IPv6 addresses. ``use_ipv6`` only affects the default DC
# used by brand-new sessions; an existing session file stores the IPv4 address
# of its home DC and Telethon connects to that stored address as-is. On hosts
# where IPv4 to Telegram is blocked, those sessions would still time out, so
# remap the stored address to the same DC's IPv6 address before connecting.
_DC_IPV6 = {
    1: "2001:b28:f23d:f001::a",
    2: "2001:67c:4e8:f002::a",
    3: "2001:b28:f23d:f003::a",
    4: "2001:67c:4e8:f004::a",
    5: "2001:b28:f23f:f005::a",
}


# Telegram's per-DC IPv4 addresses, used to undo an IPv6 remap. ``set_dc``
# persists the swapped address into the session file, so a session that was
# once connected directly over IPv6 keeps an IPv6 DC address forever — and a
# proxy cannot tunnel to it (HTTP proxies answer 503, SOCKS5 reports a general
# failure), leaving that account permanently unverifiable through its proxy.
_DC_IPV4 = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}


def _remap_session_dc_to_ipv4(client: "TelegramClient") -> None:
    sess = client.session
    dc_id = sess.dc_id
    addr = sess.server_address
    ipv4_addr = _DC_IPV4.get(dc_id)
    if not addr or ":" not in addr or not ipv4_addr:
        return  # no stored DC, already IPv4, or unknown DC
    sess.set_dc(dc_id, ipv4_addr, sess.port or 443)


def _remap_session_dc_to_ipv6(client: "TelegramClient") -> None:
    sess = client.session
    dc_id = sess.dc_id
    addr = sess.server_address
    ipv6_addr = _DC_IPV6.get(dc_id)
    if not addr or ":" in addr or not ipv6_addr:
        return  # no stored DC, already IPv6, or unknown DC
    # SQLiteSession.set_dc re-reads the auth_key for the (unchanged) DC row,
    # so the session's authorization is preserved across the address swap.
    sess.set_dc(dc_id, ipv6_addr, sess.port or 443)


def _build_candidates(*ordered: str, pool: Optional[list] = None,
                      include_direct: bool = True,
                      pool_cap: int = _MAX_POOL_CANDIDATES) -> list[str]:
    """De-duplicated egress order: the given fixed candidates, then a capped
    slice of the pool, optionally followed by a direct connection."""
    capped_pool = list(pool or [])[:pool_cap]
    candidates: list[str] = []
    all_candidates = (*ordered, *capped_pool)
    if include_direct:
        all_candidates += ("",)
    for cand in all_candidates:
        if not include_direct and not cand:
            continue
        if cand not in candidates:
            candidates.append(cand)
    return candidates


def _request_budget(client_timeout) -> tuple[float, float]:
    """Return the request budget and direct reserve for one login request.

    Older clients do not report their timeout and still use a hard-coded 30s
    HTTP timeout, so they use the conservative 22s budget. Newer clients can
    use up to 60s while retaining time for transport overhead.
    """
    if client_timeout is None:
        return _SEND_CODE_DEFAULT_BUDGET, _SEND_CODE_DEFAULT_DIRECT_RESERVE
    if (isinstance(client_timeout, bool)
            or not isinstance(client_timeout, (int, float))
            or not math.isfinite(client_timeout)
            or client_timeout <= 0
            or client_timeout > _SEND_CODE_MAX_REPORTED_TIMEOUT):
        return _SEND_CODE_DEFAULT_BUDGET, _SEND_CODE_DEFAULT_DIRECT_RESERVE

    budget = min(
        _SEND_CODE_MAX_BUDGET,
        client_timeout - _SEND_CODE_CLIENT_SAFETY_MARGIN,
    )
    if budget <= 0:
        return _SEND_CODE_DEFAULT_BUDGET, _SEND_CODE_DEFAULT_DIRECT_RESERVE
    if budget < _CONNECT_TIMEOUT + 3:
        # There is not enough time for a full proxy attempt plus a small direct
        # reserve, so spend the entire valid budget on the final direct attempt.
        return budget, budget

    reserve = max(
        _SEND_CODE_DEFAULT_DIRECT_RESERVE,
        min(_SEND_CODE_MAX_DIRECT_RESERVE, round(budget * 0.2)),
    )
    reserve = min(reserve, budget - (_CONNECT_TIMEOUT + 2))
    return budget, reserve


def _proxy_is_cooling(proxy: str) -> bool:
    if not proxy:
        return False
    record = _proxy_health.get(proxy)
    if not record:
        return False
    now = time.monotonic()
    last_failure_at = record.get("last_failure_at", now)
    if now - last_failure_at > _PROXY_FAILURE_DECAY_WINDOW:
        _proxy_health.pop(proxy, None)
        return False
    return record["cooldown_until"] > now


def _record_proxy_failure(proxy: str) -> None:
    if not proxy:
        return
    now = time.monotonic()
    record = _proxy_health.setdefault(
        proxy,
        {"failures": 0, "cooldown_until": 0.0, "last_failure_at": now},
    )
    if now - record["last_failure_at"] > _PROXY_FAILURE_DECAY_WINDOW:
        record["failures"] = 0
    record["failures"] += 1
    record["last_failure_at"] = now
    if record["failures"] >= _PROXY_FAILURE_THRESHOLD:
        record["cooldown_until"] = now + _PROXY_COOLDOWN


def _clear_proxy_failure(proxy: str) -> None:
    if proxy:
        _proxy_health.pop(proxy, None)


def _mask_proxy(proxy: str) -> str:
    """Render a proxy for logs without its credentials."""
    if not proxy:
        return "direct"
    return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", proxy)


def _filter_cooled_candidates(candidates: list[str]) -> list[str]:
    filtered = [cand for cand in candidates
                if not cand or not _proxy_is_cooling(cand)]
    # Never replace an all-cooled proxy list with direct. Callers that selected
    # proxy-only login must not create an authorization from the server IP.
    if any(cand for cand in filtered):
        return filtered
    return list(candidates)


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


# Periodic cleanup: without this, cleanup_expired only runs when a new send_code
# arrives, so a session whose verify never completes keeps its Telethon client
# connected indefinitely. A stale client whose transport dies then busy-loops
# reconnecting (retry_delay=0), starving the single-worker event loop until
# uvicorn stops accepting connections. Running cleanup on a timer bounds every
# client's lifetime to ~TTL regardless of incoming traffic.
_CLEANUP_INTERVAL = 60
_cleanup_task: Optional["asyncio.Task"] = None


async def _cleanup_loop():
    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            await cleanup_expired()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("periodic session cleanup failed")


def start_cleanup_task() -> None:
    """Start the periodic cleanup loop (idempotent). Call from an async context."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.get_event_loop().create_task(_cleanup_loop())


async def stop_cleanup_task() -> None:
    """Cancel the periodic cleanup loop if running."""
    global _cleanup_task
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None


async def send_code(
    phone: str,
    api_id: int,
    api_hash: str,
    proxy: str = "",
    proxy_pool: Optional[list] = None,
    device_model: str = "Desktop Client",
    license_key: str = "",
    client_timeout=None,
) -> dict:
    """Create a Telethon client and send verification code."""
    await cleanup_expired()
    if not 0 < api_id <= _MAX_API_ID:
        logger.warning("API_ID_INVALID_RANGE: api_id=%s", api_id)
        return {
            "success": False,
            "message": (
                f"API_ID={api_id} 无效：必须是 1 到 {_MAX_API_ID} 之间的整数"
            ),
            "api_id": api_id,
            "api_id_invalid": True,
        }
    total_budget, direct_reserve = _request_budget(client_timeout)

    if api_id <= 0 or not api_hash:
        return {"success": False, "message": "API_ID/API_HASH 未配置"}

    # Telegram may return FloodWait when the same phone is asked for another
    # code before the previous one expires. Reuse the active request during a
    # short cooldown instead of sending another code.
    now = time.time()
    for existing_sid, existing in list(_active_sessions.items()):
        if (existing["phone"] != phone
                or existing.get("license_key", "") != license_key):
            continue
        age = now - existing["created_at"]
        if age < _SEND_CODE_COOLDOWN:
            return {
                "success": True,
                "session_id": existing_sid,
                "message": "验证码已发送，请使用刚收到的验证码",
                "duplicate": True,
                "retry_after": max(1, int(_SEND_CODE_COOLDOWN - age)),
            }
        await cancel_session(existing_sid)

    session_id = secrets.token_hex(16)
    sess_path = _session_path(session_id, phone, license_key)

    proxy_configured = bool(proxy or proxy_pool)
    # A configured proxy is mandatory for login. Falling back to the server's
    # direct IP can create a Telegram authorization that is revoked shortly
    # after sign-in, leaving the user with an unusable session. Direct remains
    # available only when the caller did not configure any proxy at all.
    candidates = _filter_cooled_candidates(
        _build_candidates(
            proxy,
            pool=proxy_pool,
            include_direct=not proxy_configured,
            pool_cap=_SEND_CODE_MAX_POOL_CANDIDATES,
        ))
    if proxy_configured:
        direct_reserve = 0

    client: Optional[TelegramClient] = None
    used_proxy: str = ""
    last_conn_err = None
    start = time.monotonic()
    tried = 0
    budget_exceeded = False
    for idx, cand in enumerate(candidates):
        is_last = idx == len(candidates) - 1
        remaining = total_budget - (time.monotonic() - start)
        if not is_last and remaining <= direct_reserve:
            continue
        if remaining <= 0:
            budget_exceeded = True
            break
        tried += 1
        c = _make_client(sess_path, api_id, api_hash, cand,
                         device_model=device_model or "Desktop Client",
                         retries=_LOGIN_CONNECT_RETRIES)
        try:
            cap = remaining if is_last else remaining - direct_reserve
            per_cand = min(_CONNECT_TIMEOUT + 2, max(1.0, cap))
            await asyncio.wait_for(c.connect(), timeout=per_cand)
        except Exception as e:
            # This egress could not reach Telegram (proxy down / network).
            # Try the next candidate before giving up.
            last_conn_err = e
            _record_proxy_failure(cand)
            try:
                await c.disconnect()
            except Exception:
                pass
            continue
        _clear_proxy_failure(cand)
        client = c
        used_proxy = cand
        break

    if client is None:
        _cleanup_session_files(sess_path)
        if budget_exceeded or time.monotonic() - start >= total_budget:
            logger.warning(
                "send_code for %s: 连接超时预算 %ss 用尽（尝试 %d 个出口）",
                phone, total_budget, tried,
            )
            if proxy_configured:
                message = (
                    f"所有代理均不可用（{total_budget}s 内已尝试 "
                    f"{tried} 个），为避免裸 IP 登录导致新号被封，"
                    "请重新发送"
                )
            else:
                message = (
                    f"代理连接超时（{total_budget}s 内尝试了 "
                    f"{tried} 个出口），请重新发送"
                )
            return {"success": False, "message": message}
        if proxy_configured:
            logger.warning(
                "send_code for %s: all proxies unavailable; refusing direct "
                "fallback to avoid a bare-IP login",
                phone,
            )
            return {
                "success": False,
                "message": (
                    f"所有代理均不可用（已尝试 {tried} 个），"
                    "为避免裸 IP 登录导致新号被封，请重新发送"
                ),
            }
        return {
            "success": False,
            "message": f"无法连接 Telegram（代理与直连均失败）: {last_conn_err}",
        }

    try:
        # Persist the proxy this session's auth was created through, so a later
        # health check can reuse the SAME egress (and fall back to direct if the
        # proxy dies) instead of relying on the client to re-send it.
        _save_session_proxy(sess_path, used_proxy)
        remaining = total_budget - (time.monotonic() - start)
        if remaining <= 0:
            raise asyncio.TimeoutError
        sent = await asyncio.wait_for(
            client.send_code_request(phone), timeout=remaining)
        _active_sessions[session_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "created_at": time.time(),
            "api_id": api_id,
            "api_hash": api_hash,
            "license_key": license_key,
        }
        logger.info(f"Code sent for {phone}, session_id={session_id[:8]}...")
        result = {
            "success": True,
            "session_id": session_id,
            "message": f"验证码已发送到 {phone}",
        }
        return result
    except PhoneNumberInvalidError:
        await client.disconnect()
        _cleanup_session_files(sess_path)
        return {"success": False, "message": "手机号格式无效"}
    except ApiIdInvalidError:
        await client.disconnect()
        _cleanup_session_files(sess_path)
        logger.warning("API_ID_INVALID: api_id=%s", api_id)
        return {
            "success": False,
            "message": f"API_ID={api_id} 的 API_ID/API_HASH 组合无效",
            "api_id": api_id,
            "api_id_invalid": True,
        }
    except FloodWaitError as e:
        logger.warning(
            "FloodWait on send_code: phone=%s api_id=%s wait_seconds=%s",
            phone,
            api_id,
            e.seconds,
        )
        await client.disconnect()
        _cleanup_session_files(sess_path)
        return {"success": False, "message": f"请求过于频繁，请等待 {e.seconds} 秒"}
    except PhonePasswordFloodError:
        await client.disconnect()
        _cleanup_session_files(sess_path)
        return {"success": False, "message": "该号码登录尝试次数过多，请稍后再试"}
    except asyncio.TimeoutError:
        await client.disconnect()
        _cleanup_session_files(sess_path)
        logger.warning(
            "send_code for %s: 代理连接或 Telegram 请求超过 %ss 总预算",
            phone, total_budget,
        )
        return {
            "success": False,
            "message": (
                f"代理不可用或连接超时（超过 {total_budget}s 总预算），"
                "请重新发送"
            ),
        }
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
                              license_key: str = "",
                              client_timeout=None) -> dict:
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
    #   4) direct connection only for sessions that were originally direct
    # A proxy-backed authorization must not be checked over the server's bare
    # IP after its original proxy fails.
    # A proxy value of "" means direct.
    login_proxy = _load_session_proxy(base_path)
    candidates = _filter_cooled_candidates(
        _build_candidates(
            proxy,
            login_proxy,
            pool=proxy_pool,
            include_direct=not bool(login_proxy),
        ))

    last_conn_err = None
    # Remember any non-empty proxy that failed to connect, so that when the
    # check ultimately succeeds over a direct connection we can report that the
    # proxy died and we fell back (``proxy_fell_back``) — the caller uses this
    # to warn the group.
    failed_proxy = ""
    start = time.monotonic()
    tried = 0
    budget_exceeded = False
    total_budget, _ = _request_budget(client_timeout)
    # The reserve exists to guarantee the direct fallback a slot. A proxy-only
    # chain has nothing to protect, so reserving there just throws away budget
    # that could have tried another proxy.
    reserve = _DIRECT_RESERVE if candidates and candidates[-1] == "" else 0
    for idx, cand in enumerate(candidates):
        is_last = idx == len(candidates) - 1
        remaining = total_budget - (time.monotonic() - start)
        if not is_last and remaining <= reserve:
            # Not enough budget left for this proxy AND the final direct
            # attempt — skip ahead so direct always gets its reserved slot.
            continue
        if remaining <= 0:
            budget_exceeded = True
            break
        tried += 1
        client = _make_client(base_path, api_id, api_hash, cand)
        try:
            try:
                # Cap each candidate to _CONNECT_TIMEOUT so a single dead proxy
                # can't eat the whole budget and starve the fallback candidates
                # (especially the direct connection). Non-final candidates also
                # leave _DIRECT_RESERVE for the final direct attempt.
                cap = remaining if is_last else remaining - reserve
                per_cand = min(_CONNECT_TIMEOUT + 2, max(1.0, cap))
                await asyncio.wait_for(client.connect(), timeout=per_cand)
            except Exception as e:
                # Could not reach Telegram through this egress (proxy down,
                # network, DC unreachable). Try the next candidate before giving
                # up — a dead proxy must not look like an invalid session.
                last_conn_err = e
                _record_proxy_failure(cand)
                if cand and not failed_proxy:
                    failed_proxy = cand
                continue
            _clear_proxy_failure(cand)
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

    # Either every egress failed to connect, or we ran out of the time budget
    # before exhausting them. Both say nothing about the session -> inconclusive.
    if budget_exceeded:
        logger.warning(
            "check_session for %s: 连接超时预算 %ss 用尽（尝试 %d 个出口），返回未能确认",
            phone, total_budget, tried,
        )
        message = (
            f"代理连接超时（{total_budget}s 内尝试了 {tried} 个出口均连不上），"
            "未能确认，请检查该账号的代理"
        )
    else:
        # This branch used to return silently, so a client showing "代理均失败"
        # left no trace on the server at all and the only countable symptom,
        # the budget warning above, stayed at zero. Log the egresses that were
        # actually tried (credentials stripped) so a bad proxy is identifiable.
        logger.warning(
            "check_session for %s: 尝试 %d 个出口全部连接失败 [%s]，返回未能确认: %s",
            phone, tried,
            ", ".join(_mask_proxy(c) for c in candidates[:tried]),
            last_conn_err,
        )
        if login_proxy:
            message = (
                f"无法连接 Telegram（尝试了 {tried} 个代理，代理均失败），"
                f"未能验证: {last_conn_err}"
            )
        else:
            message = (
                f"无法连接 Telegram（尝试了 {tried} 个出口，代理与直连均失败），"
                f"未能验证: {last_conn_err}"
            )
    return {
        "success": False,
        "inconclusive": True,
        "message": message,
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
    client = None
    deadline = time.monotonic() + _TDATA_TOTAL_TIMEOUT

    try:
        tdata_dir = os.path.join(temp_dir, "tdata")
        os.makedirs(tdata_dir, exist_ok=True)

        base_session_path = sess_path
        if base_session_path.endswith(".session"):
            base_session_path = base_session_path[:-8]

        login_proxy = _load_session_proxy(base_session_path)
        client_kwargs = {
            "proxy": _parse_proxy(login_proxy),
            "timeout": _CONNECT_TIMEOUT,
            "connection_retries": _LOGIN_CONNECT_RETRIES,
            "retry_delay": 0,
            "use_ipv6": _USE_IPV6 and not login_proxy,
        }
        client = OTelegramClient(base_session_path, **client_kwargs)
        if client_kwargs["use_ipv6"]:
            _remap_session_dc_to_ipv6(client)
        elif login_proxy:
            _remap_session_dc_to_ipv4(client)

        try:
            async def convert():
                await client.connect()
                tdesk = await client.ToTDesktop(flag=UseCurrentSession)
                tdesk.SaveTData(tdata_dir)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            await asyncio.wait_for(convert(), timeout=remaining)
        finally:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:
                pass

        zip_base = os.path.join(temp_dir, f"{safe_phone}_{ts}_tdata")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        zip_filepath = await asyncio.wait_for(
            asyncio.to_thread(
                shutil.make_archive,
                zip_base,
                "zip",
                temp_dir,
                "tdata",
            ),
            timeout=remaining,
        )

        shutil.move(zip_filepath, zip_path)
        logger.info(f"tdata zip created: {zip_path}")
        return zip_path
    except asyncio.TimeoutError:
        logger.error(
            "tdata conversion timed out for %s after %ss",
            phone,
            _TDATA_TOTAL_TIMEOUT,
        )
        return None
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
