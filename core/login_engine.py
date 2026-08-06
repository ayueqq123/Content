"""Remote login engine — HTTP client that delegates Telethon work to the server.

The desktop client never imports or runs Telethon.  All login operations
are forwarded to the license server's ``/api/login/*`` endpoints.
"""

import os
import re
import json
import random
import logging

import httpx

from core.crypto import decrypt as _d

logger = logging.getLogger(__name__)

# When a phone's bound proxy is dead, only a handful of pool alternates are
# offered to the server before it degrades to direct. Sending the entire pool
# (which can be hundreds of proxies) made the server try each one sequentially,
# turning a single dead proxy into a multi-minute send_code stall.
_MAX_PROXY_ALTERNATES = 3


def parse_api_pool(raw: str) -> list[tuple[int, str]]:
    """Parse a multi-line API_ID pool into a de-duplicated list of (api_id, api_hash).

    One credential per line as ``api_id:api_hash`` (``,`` or whitespace also
    accepted as the separator). Blank lines and lines starting with ``#`` are
    ignored, as are malformed lines (non-integer api_id / missing api_hash).
    """
    if not raw:
        return []
    out: list[tuple[int, str]] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = re.split(r"[:,\s]+", s, maxsplit=1)
        if len(parts) < 2:
            continue
        try:
            api_id = int(parts[0])
        except ValueError:
            continue
        api_hash = parts[1].strip()
        if api_id <= 0 or not api_hash:
            continue
        pair = (api_id, api_hash)
        if pair not in out:
            out.append(pair)
    return out


def load_api_pool_from_file(path: str) -> list[tuple[int, str]]:
    """Read a txt API pool file (``api_id:api_hash`` per line), de-duplicated.

    A missing or unreadable file yields an empty list (falls back to other
    API credential sources).
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_api_pool(f.read())
    except Exception as e:
        logger.warning(f"读取 API 文件失败 ({path}): {e}")
        return []


def build_api_pool(api_file: str, api_text: str) -> list[tuple[int, str]]:
    """Merge API credentials from a txt file and the manual pool box, de-duplicated.

    Credentials from the uploaded ``api_file`` come first, then any extra ones
    typed into the manual pool box.
    """
    out = load_api_pool_from_file(api_file)
    for pair in parse_api_pool(api_text):
        if pair not in out:
            out.append(pair)
    return out


def parse_proxy_list(raw: str) -> list[str]:
    """Parse a multi-line proxy pool string into a de-duplicated list.

    One proxy per line; blank lines and lines starting with ``#`` are ignored.
    """
    if not raw:
        return []
    out: list[str] = []
    for line in raw.splitlines():
        p = line.strip()
        if not p or p.startswith("#"):
            continue
        if p not in out:
            out.append(p)
    return out


def load_proxies_from_file(path: str) -> list[str]:
    """Read a txt proxy file (one proxy per line) into a de-duplicated list.

    Blank lines and lines starting with ``#`` are ignored. A missing or
    unreadable file yields an empty list (falls back to other proxy sources).
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_proxy_list(f.read())
    except Exception as e:
        logger.warning(f"读取代理文件失败 ({path}): {e}")
        return []


def build_proxy_list(proxy_file: str, proxy_text: str) -> list[str]:
    """Merge proxies from a txt file and the manual pool textbox, de-duplicated.

    Proxies from the uploaded ``proxy_file`` come first, then any extra ones
    typed into the manual pool textbox. This lets the whole proxy pool be
    supplied by a single txt upload while still honouring manual entries.
    """
    out = load_proxies_from_file(proxy_file)
    for p in parse_proxy_list(proxy_text):
        if p not in out:
            out.append(p)
    return out

# API paths (encrypted to resist static analysis)
_EP_SEND_CODE = _d("7HmjkZd8x73Dgy8aeRsNp06OPl8=")          # /api/login/send_code
_EP_VERIFY_CODE = _d("7HmjkZd8x73Dgy8feQcAnlS+OVXKVA==")     # /api/login/verify_code
_EP_VERIFY_2FA = _d("7HmjkZd8x73Dgy8feQcAnlS+aFzP")          # /api/login/verify_2fa
_EP_DOWNLOAD = _d("7HmjkZd8x73Dgy8NcwIHlEKAPmXdVBxPBmiW")    # /api/login/download_session
_EP_DOWNLOAD_TDATA = _d("7HmjkZd8x73Dgy8NcwIHlEKAPmXaVQ5IDg==")  # /api/login/download_tdata
_EP_CANCEL = _d("7HmjkZd8x73Dgy8KfRsKnUE=")                  # /api/login/cancel


def proxy_to_url(proxy_str: str) -> str:
    """Convert proxy string to URL format for aiohttp-socks."""
    if not proxy_str:
        return ""
    if "://" in proxy_str:
        return proxy_str
    parts = proxy_str.split(":")
    if len(parts) < 2:
        return proxy_str
    host, port = parts[0], parts[1]
    if len(parts) >= 4:
        user, pwd = parts[2], parts[3]
        return f"socks5://{user}:{pwd}@{host}:{port}"
    return f"socks5://{host}:{port}"


class LoginEngine:
    """Thin HTTP wrapper around the server-side login service."""

    def __init__(self):
        self._server_url: str = ""
        self._license_key: str = ""
        self._hwid: str = ""
        self._api_id: int = 0
        self._api_hash: str = ""
        self._api_pool: list[tuple[int, str]] = []
        self._proxy: str = ""
        self._proxy_list: list[str] = []
        self._device_model: str = ""
        self._sessions_dir: str = ""
        # phone -> server session_id
        self._pending: dict[str, str] = {}
        # phone -> proxy assigned at login time (persisted, reused for checks)
        self._phone_proxy: dict[str, str] = {}
        # round-robin cursor into _proxy_list
        self._rr_index: int = 0
        # phone -> (api_id, api_hash) assigned at login (persisted, reused for checks)
        self._phone_api: dict[str, tuple[int, str]] = {}
        # round-robin cursor into _api_pool
        self._api_rr_index: int = 0

    def configure(self, sessions_dir: str, proxy: str, device_model: str,
                  api_id: int, api_hash: str, proxy_list: list[str] | None = None,
                  api_pool: list[tuple[int, str]] | None = None):
        self._sessions_dir = sessions_dir
        self._proxy = proxy
        self._proxy_list = proxy_list or []
        self._device_model = device_model or "Desktop Client"
        self._api_id = api_id
        self._api_hash = api_hash
        self._api_pool = api_pool or []
        os.makedirs(self._sessions_dir, exist_ok=True)
        self._load_proxy_map()
        self._load_api_map()

    # ── per-account proxy assignment (防关联) ────────────────────────────
    @property
    def _proxy_map_path(self) -> str:
        return os.path.join(self._sessions_dir, "proxy_map.json")

    def _load_proxy_map(self):
        try:
            with open(self._proxy_map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._phone_proxy = {str(k): str(v) for k, v in data.items()}
                # Continue the rotation past already-assigned phones so a
                # restart doesn't restart the pool from index 0.
                self._rr_index = len(self._phone_proxy)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"读取代理映射失败: {e}")

    def _save_proxy_map(self):
        try:
            with open(self._proxy_map_path, "w", encoding="utf-8") as f:
                json.dump(self._phone_proxy, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存代理映射失败: {e}")

    def _proxy_for(self, phone: str) -> str:
        """Return the proxy this phone should use.

        A phone keeps whatever proxy it was first assigned (so its login egress
        and later health-check egress stay identical — a changing exit IP makes
        Telegram reject an otherwise-valid session). New phones are handed the
        next proxy in the pool, round-robin, so many accounts are spread across
        different IPs instead of all sharing one. Falls back to the single
        ``_proxy`` (then direct) when no pool is configured.
        """
        existing = self._phone_proxy.get(phone)
        if existing is not None:
            return existing
        if self._proxy_list:
            assigned = self._proxy_list[self._rr_index % len(self._proxy_list)]
            self._rr_index += 1
        else:
            assigned = self._proxy
        self._phone_proxy[phone] = assigned
        self._save_proxy_map()
        if self._proxy_list:
            host = assigned.split(":")[0] if assigned else "直连"
            logger.info(f"账号 {phone} 分配代理出口: {host} "
                        f"({len(self._phone_proxy)}/{len(self._proxy_list)} 已分配)")
        return assigned

    def _proxy_alternates_for(self, phone: str) -> list[str]:
        """Return the other pool proxies to try if this phone's bound proxy dies.

        Excludes the phone's own bound proxy and blanks, and is capped at
        ``_MAX_PROXY_ALTERNATES`` (randomly sampled) so a dead bound proxy costs
        at most a few extra connection attempts before the server degrades to
        direct, instead of walking the whole pool.
        """
        bound = self._proxy_for(phone)
        if not self._proxy_list:
            return []
        others = [p for p in self._proxy_list if p and p != bound]
        if len(others) <= _MAX_PROXY_ALTERNATES:
            return others
        return random.sample(others, _MAX_PROXY_ALTERNATES)

    # ── per-account API_ID assignment (防发码限流) ───────────────────────
    @property
    def _api_map_path(self) -> str:
        return os.path.join(self._sessions_dir, "api_map.json")

    def _load_api_map(self):
        try:
            with open(self._api_map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._phone_api = {
                    str(k): (int(v[0]), str(v[1]))
                    for k, v in data.items()
                    if isinstance(v, (list, tuple)) and len(v) == 2
                }
                # Continue the rotation past already-assigned phones so a
                # restart doesn't restart the pool from index 0.
                self._api_rr_index = len(self._phone_api)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"读取 API 映射失败: {e}")

    def _save_api_map(self):
        try:
            with open(self._api_map_path, "w", encoding="utf-8") as f:
                json.dump({k: [v[0], v[1]] for k, v in self._phone_api.items()},
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存 API 映射失败: {e}")

    def _api_for(self, phone: str) -> tuple[int, str]:
        """Return the (api_id, api_hash) this phone should use.

        A phone keeps whatever API credential it was first assigned — a session
        authorized under one api_id must keep being used with that same api_id,
        otherwise Telegram rejects it. New phones are handed the next credential
        in the pool, round-robin, so many accounts are spread across different
        API_IDs instead of all hammering one (which triggers send-code
        FloodWait). Falls back to the single ``_api_id``/``_api_hash`` when no
        pool is configured.
        """
        existing = self._phone_api.get(phone)
        if existing is not None:
            return existing
        if self._api_pool:
            assigned = self._api_pool[self._api_rr_index % len(self._api_pool)]
            self._api_rr_index += 1
        else:
            assigned = (self._api_id, self._api_hash)
        self._phone_api[phone] = assigned
        self._save_api_map()
        if self._api_pool:
            logger.info(f"账号 {phone} 分配 API_ID: {assigned[0]} "
                        f"({len(self._phone_api)}/{len(self._api_pool)} 已分配)")
        return assigned

    def _reassign_api(self, phone: str) -> tuple[int, str] | None:
        """Remove the current API binding for *phone* and assign the next one
        from the pool.  Returns the new (api_id, api_hash), or ``None`` if the
        pool is exhausted (every credential has already been tried for this
        phone in the current retry cycle).
        """
        if not self._api_pool or len(self._api_pool) <= 1:
            return None
        old = self._phone_api.pop(phone, None)
        if self._api_pool:
            new = self._api_pool[self._api_rr_index % len(self._api_pool)]
            self._api_rr_index += 1
            # Don't reassign the same credential we just failed with.
            if old is not None and new == old and len(self._api_pool) > 1:
                new = self._api_pool[self._api_rr_index % len(self._api_pool)]
                self._api_rr_index += 1
        else:
            return None
        self._phone_api[phone] = new
        self._save_api_map()
        logger.info(f"账号 {phone} API 限流回退: {old[0] if old else '?'} -> {new[0]}")
        return new

    def set_server(self, server_url: str, license_key: str, hwid: str):
        """Set the remote server URL and credentials."""
        self._server_url = server_url.rstrip("/")
        self._license_key = license_key
        self._hwid = hwid

    def _url(self, endpoint: str) -> str:
        return self._server_url + endpoint

    async def _post(self, endpoint: str, json_data: dict, timeout: int = 90) -> dict:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._url(endpoint), json=json_data)
                try:
                    data = resp.json()
                except Exception:
                    data = None

                # Normal, well-formed answer from our own endpoints.
                if isinstance(data, dict) and "success" in data:
                    return data

                # The server answered with an HTTP error status. FastAPI's
                # HTTPException body is ``{"detail": "..."}`` — it has NO
                # ``success`` field, so surface that real reason (e.g.
                # "卡密无效或已过期") instead of the misleading generic message.
                if resp.is_error:
                    detail = None
                    if isinstance(data, dict):
                        detail = data.get("detail") or data.get("message")
                    msg = detail or f"服务器返回 HTTP {resp.status_code}"
                    result = {"success": False,
                              "message": f"{msg}（HTTP {resp.status_code}）"}
                    # A 5xx means the server itself failed — it says NOTHING
                    # about the account's session state, so flag it as
                    # inconclusive (see the transport-failure note below).
                    if resp.status_code >= 500:
                        result["inconclusive"] = True
                    return result

                if not isinstance(data, dict):
                    return {"success": False, "message": "服务器返回格式异常"}

                # 2xx but the payload is a dict without ``success``.
                data["success"] = False
                data.setdefault("message", "服务器响应格式异常（缺少 success 字段）")
                return data
        except Exception as e:
            logger.error(f"Remote call failed ({endpoint}): {e}")
            # A transport-level failure (timeout / connection refused / DNS)
            # means we never got an answer from the server, so it says NOTHING
            # about the account's session state. Flag it as inconclusive so the
            # periodic session check does not miscount it as evidence that the
            # session is invalid (which would falsely report "session 已失效").
            return {"success": False, "inconclusive": True,
                    "message": f"网络请求失败: {e}"}

    # Error keywords that indicate the current API credential is flood-limited
    # and a different one from the pool should be tried.
    _API_FLOOD_KEYWORDS = ("登录尝试次数过多", "请求过于频繁", "FloodWait",
                           "PhonePasswordFlood", "too many times")

    def _is_api_flood(self, result: dict) -> bool:
        """Return True if *result* indicates an API-level flood that may be
        resolved by switching to a different api_id from the pool."""
        if result.get("success"):
            return False
        msg = result.get("message", "")
        return any(kw in msg for kw in self._API_FLOOD_KEYWORDS)

    def _is_api_invalid(self, result: dict) -> bool:
        """Return True if the current API credential is rejected by Telegram."""
        if result.get("success"):
            return False
        if result.get("api_id_invalid"):
            return True
        return "API_ID/API_HASH 组合无效" in result.get("message", "")

    async def send_code(self, phone: str) -> dict:
        if not self._server_url:
            return {"success": False, "message": "未配置远程登录服务器"}
        api_id, api_hash = self._api_for(phone)
        if api_id <= 0 or not api_hash:
            return {"success": False, "message": "API_ID/API_HASH 未配置"}

        request_timeout = 90
        result = await self._post(_EP_SEND_CODE, {
            "phone": phone,
            "api_id": api_id,
            "api_hash": api_hash,
            "proxy": self._proxy_for(phone),
            "proxy_pool": self._proxy_alternates_for(phone),
            "device_model": self._device_model,
            "license_key": self._license_key,
            "hwid": self._hwid,
            "client_timeout": request_timeout,
        }, timeout=request_timeout)

        # Retry with a different API credential when the current one is
        # flood-limited or rejected as invalid.
        if ((self._is_api_flood(result) or self._is_api_invalid(result))
                and self._api_pool and len(self._api_pool) > 1):
            max_retries = min(len(self._api_pool) - 1, 3)
            for attempt in range(max_retries):
                new_api = self._reassign_api(phone)
                if new_api is None:
                    break
                reason = "API_ID 无效" if self._is_api_invalid(result) else "API 限流"
                logger.warning(f"{phone} {reason}，第 {attempt + 1} 次回退重试 "
                               f"API_ID={new_api[0]}")
                result = await self._post(_EP_SEND_CODE, {
                    "phone": phone,
                    "api_id": new_api[0],
                    "api_hash": new_api[1],
                    "proxy": self._proxy_for(phone),
                    "proxy_pool": self._proxy_alternates_for(phone),
                    "device_model": self._device_model,
                    "license_key": self._license_key,
                    "hwid": self._hwid,
                    "client_timeout": request_timeout,
                }, timeout=request_timeout)
                if not (self._is_api_flood(result) or self._is_api_invalid(result)):
                    break

        if result.get("success"):
            self._pending[phone] = result["session_id"]

        return result

    async def sign_in_with_code(self, phone: str, code: str) -> dict:
        sid = self._pending.get(phone)
        if not sid:
            return {"success": False, "message": "请先发送验证码"}

        result = await self._post(_EP_VERIFY_CODE, {
            "session_id": sid,
            "phone": phone,
            "code": code,
            "license_key": self._license_key,
        })

        if result.get("success"):
            self._pending.pop(phone, None)
            # Download session file from server
            await self._download_session(sid, phone)

        return result

    async def sign_in_with_2fa(self, phone: str, password: str) -> dict:
        sid = self._pending.get(phone)
        if not sid:
            return {"success": False, "message": "请先发送验证码并输入验证码"}

        result = await self._post(_EP_VERIFY_2FA, {
            "session_id": sid,
            "phone": phone,
            "password": password,
            "license_key": self._license_key,
        })

        if result.get("success"):
            self._pending.pop(phone, None)
            await self._download_session(sid, phone)

        return result

    async def _download_session(self, session_id: str, phone: str):
        """Download the tdata zip from the server; fall back to raw .session."""
        safe_phone = phone.replace("+", "").replace(" ", "")

        # Try tdata zip first
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    self._url(_EP_DOWNLOAD_TDATA),
                    json={"session_id": session_id, "phone": phone,
                          "license_key": self._license_key},
                )
                if resp.status_code == 200:
                    fname = None
                    cd = resp.headers.get("content-disposition", "")
                    if "filename=" in cd:
                        fname = cd.split("filename=")[-1].strip('"')
                    if not fname:
                        from datetime import datetime
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        fname = f"{safe_phone}_{ts}_tdata.zip"
                    local_path = os.path.join(self._sessions_dir, fname)
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    logger.info(f"tdata zip downloaded: {local_path}")
                    return
                else:
                    logger.warning(f"tdata download failed ({resp.status_code}), falling back to session")
        except Exception as e:
            logger.warning(f"tdata download error: {e}, falling back to session")

        # Fallback: raw session file
        try:
            local_path = os.path.join(self._sessions_dir, f"{safe_phone}.session")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self._url(_EP_DOWNLOAD),
                    json={"session_id": session_id, "phone": phone,
                          "license_key": self._license_key},
                )
                if resp.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    logger.info(f"Session downloaded: {local_path}")
                else:
                    logger.warning(f"Session download failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"Session download error: {e}")

    def get_last_session_path(self, phone: str) -> str | None:
        """Return the local path of the latest tdata zip or session file."""
        import glob
        safe_phone = phone.replace("+", "").replace(" ", "")
        # Prefer tdata zip
        pattern = os.path.join(self._sessions_dir, f"{safe_phone}_*_tdata.zip")
        zips = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if zips:
            return zips[0]
        # Fallback: raw session
        path = os.path.join(self._sessions_dir, f"{safe_phone}.session")
        if os.path.isfile(path):
            return path
        return None

    async def check_session(self, phone: str) -> dict:
        """Check if session is still valid by asking the server to verify."""
        if not self._server_url:
            return {"success": False, "message": "未配置远程服务器"}
        api_id, api_hash = self._api_for(phone)
        if api_id <= 0 or not api_hash:
            return {"success": False, "message": "API_ID/API_HASH 未配置"}

        _EP_CHECK = "/api/login/check_session"
        result = await self._post(_EP_CHECK, {
            "phone": phone,
            "api_id": api_id,
            "api_hash": api_hash,
            "proxy": self._proxy_for(phone),
            "proxy_pool": self._proxy_alternates_for(phone),
            "license_key": self._license_key,
            "hwid": self._hwid,
            "client_timeout": 90,
        }, timeout=90)
        return result

    async def cancel_pending(self, phone: str):
        sid = self._pending.pop(phone, None)
        if sid:
            await self._post(_EP_CANCEL, {"session_id": sid, "phone": phone, "license_key": self._license_key})

    async def cleanup(self):
        for phone, sid in list(self._pending.items()):
            await self._post(_EP_CANCEL, {"session_id": sid, "phone": phone, "license_key": self._license_key})
        self._pending.clear()


login_engine = LoginEngine()
