"""Remote login engine — HTTP client that delegates Telethon work to the server.

The desktop client never imports or runs Telethon.  All login operations
are forwarded to the license server's ``/api/login/*`` endpoints.
"""

import os
import logging

import httpx

from core.crypto import decrypt as _d

logger = logging.getLogger(__name__)

# API paths (encrypted to resist static analysis)
_EP_SEND_CODE = _d("7HmjkZd8x73Dgy8aeRsNp06OPl8=")          # /api/login/send_code
_EP_VERIFY_CODE = _d("7HmjkZd8x73Dgy8feQcAnlS+OVXKVA==")     # /api/login/verify_code
_EP_VERIFY_2FA = _d("7HmjkZd8x73Dgy8feQcAnlS+aFzP")          # /api/login/verify_2fa
_EP_DOWNLOAD = _d("7HmjkZd8x73Dgy8NcwIHlEKAPmXdVBxPBmiW")    # /api/login/download_session
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
        self._proxy: str = ""
        self._device_model: str = ""
        self._sessions_dir: str = ""
        # phone -> server session_id
        self._pending: dict[str, str] = {}

    def configure(self, sessions_dir: str, proxy: str, device_model: str,
                  api_id: int, api_hash: str):
        self._sessions_dir = sessions_dir
        self._proxy = proxy
        self._device_model = device_model or "Desktop Client"
        self._api_id = api_id
        self._api_hash = api_hash
        os.makedirs(self._sessions_dir, exist_ok=True)

    def set_server(self, server_url: str, license_key: str, hwid: str):
        """Set the remote server URL and credentials."""
        self._server_url = server_url.rstrip("/")
        self._license_key = license_key
        self._hwid = hwid

    def _url(self, endpoint: str) -> str:
        return self._server_url + endpoint

    def _post(self, endpoint: str, json_data: dict, timeout: int = 30) -> dict:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(self._url(endpoint), json=json_data)
                return resp.json()
        except Exception as e:
            logger.error(f"Remote call failed ({endpoint}): {e}")
            return {"success": False, "message": f"网络请求失败: {e}"}

    async def send_code(self, phone: str) -> dict:
        if not self._server_url:
            return {"success": False, "message": "未配置远程登录服务器"}
        if self._api_id <= 0 or not self._api_hash:
            return {"success": False, "message": "API_ID/API_HASH 未配置"}

        # Cancel any existing session for this phone
        old_sid = self._pending.pop(phone, None)
        if old_sid:
            self._post(_EP_CANCEL, {"session_id": old_sid, "phone": phone})

        result = self._post(_EP_SEND_CODE, {
            "phone": phone,
            "api_id": self._api_id,
            "api_hash": self._api_hash,
            "proxy": self._proxy,
            "device_model": self._device_model,
            "license_key": self._license_key,
            "hwid": self._hwid,
        })

        if result.get("success"):
            self._pending[phone] = result["session_id"]

        return result

    async def sign_in_with_code(self, phone: str, code: str) -> dict:
        sid = self._pending.get(phone)
        if not sid:
            return {"success": False, "message": "请先发送验证码"}

        result = self._post(_EP_VERIFY_CODE, {
            "session_id": sid,
            "phone": phone,
            "code": code,
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

        result = self._post(_EP_VERIFY_2FA, {
            "session_id": sid,
            "phone": phone,
            "password": password,
        })

        if result.get("success"):
            self._pending.pop(phone, None)
            await self._download_session(sid, phone)

        return result

    async def _download_session(self, session_id: str, phone: str):
        """Download the session file from the server and save locally."""
        try:
            safe_phone = phone.replace("+", "").replace(" ", "")
            local_path = os.path.join(self._sessions_dir, f"{safe_phone}.session")

            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    self._url(_EP_DOWNLOAD),
                    json={"session_id": session_id, "phone": phone},
                )
                if resp.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    logger.info(f"Session downloaded: {local_path}")
                else:
                    logger.warning(f"Session download failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"Session download error: {e}")

    async def check_session(self, phone: str) -> dict:
        safe_phone = phone.replace("+", "").replace(" ", "")
        session_path = os.path.join(self._sessions_dir, f"{safe_phone}.session")
        if not os.path.exists(session_path):
            return {"success": False, "message": "Session 文件不存在"}
        return {"success": True, "message": "Session 文件存在（本地）"}

    async def cancel_pending(self, phone: str):
        sid = self._pending.pop(phone, None)
        if sid:
            self._post(_EP_CANCEL, {"session_id": sid, "phone": phone})

    async def cleanup(self):
        for phone, sid in list(self._pending.items()):
            self._post(_EP_CANCEL, {"session_id": sid, "phone": phone})
        self._pending.clear()


login_engine = LoginEngine()
