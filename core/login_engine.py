"""Telethon login engine adapted for the desktop app."""

import os
import asyncio
import logging
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


def parse_proxy(proxy_str: str) -> tuple | None:
    """Parse a proxy string into the tuple Telethon expects.

    Supported formats:
      - URL:  socks5://user:pass@host:port  /  http://host:port
      - Plain:  host:port:user:pass  (treated as SOCKS5)
      - Plain without auth:  host:port  (treated as SOCKS5)
    """
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
    def __init__(self):
        self._pending: dict[str, dict] = {}
        self._proxy: tuple | None = None
        self._sessions_dir: str = ""
        self._device_model: str = ""
        self._api_id: int = 0
        self._api_hash: str = ""

    def configure(self, sessions_dir: str, proxy: str, device_model: str,
                  api_id: int, api_hash: str):
        self._sessions_dir = sessions_dir
        self._proxy = parse_proxy(proxy)
        self._device_model = device_model or "Desktop Client"
        self._api_id = api_id
        self._api_hash = api_hash
        os.makedirs(self._sessions_dir, exist_ok=True)

    def _session_path(self, phone: str) -> str:
        safe = phone.replace("+", "").replace(" ", "")
        return os.path.join(self._sessions_dir, safe)

    def _temp_session_path(self, phone: str) -> str:
        safe = phone.replace("+", "").replace(" ", "")
        return os.path.join(self._sessions_dir, f"_tmp_{safe}")

    def _make_client(self, session_path: str) -> TelegramClient:
        return TelegramClient(
            session_path,
            api_id=self._api_id,
            api_hash=self._api_hash,
            proxy=self._proxy,
            device_model=self._device_model,
        )

    async def _promote_temp_session(self, phone: str):
        tmp = self._temp_session_path(phone) + ".session"
        final = self._session_path(phone) + ".session"
        if not os.path.exists(tmp):
            return
        for _ in range(15):
            try:
                os.replace(tmp, final)
                return
            except PermissionError:
                await asyncio.sleep(0.2)
            except Exception:
                break

    async def _cleanup_temp_session(self, phone: str):
        base = self._temp_session_path(phone)
        suffixes = [".session", ".session-journal", ".session-wal", ".session-shm"]
        for suffix in suffixes:
            path = base + suffix
            if not os.path.exists(path):
                continue
            for _ in range(10):
                try:
                    os.remove(path)
                    break
                except PermissionError:
                    await asyncio.sleep(0.2)
                except Exception:
                    break

    async def send_code(self, phone: str) -> dict:
        if self._api_id <= 0 or not self._api_hash:
            return {"success": False, "message": "API_ID/API_HASH 未配置"}

        pending = self._pending.get(phone)
        if pending and pending.get("client"):
            try:
                await pending["client"].disconnect()
            except Exception:
                pass
            self._pending.pop(phone, None)

        await self._cleanup_temp_session(phone)
        client = self._make_client(self._temp_session_path(phone))
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
            self._pending[phone] = {
                "client": client,
                "phone_code_hash": sent.phone_code_hash,
            }
            return {"success": True, "message": f"验证码已发送到 {phone}"}
        except PhoneNumberInvalidError:
            await client.disconnect()
            await self._cleanup_temp_session(phone)
            return {"success": False, "message": "手机号格式无效"}
        except ApiIdInvalidError:
            await client.disconnect()
            await self._cleanup_temp_session(phone)
            return {"success": False, "message": "API_ID/API_HASH 组合无效"}
        except FloodWaitError as e:
            await client.disconnect()
            await self._cleanup_temp_session(phone)
            return {"success": False, "message": f"请求过于频繁，请等待 {e.seconds} 秒"}
        except Exception as e:
            logger.exception(f"send_code failed for {phone}")
            await client.disconnect()
            await self._cleanup_temp_session(phone)
            return {"success": False, "message": f"发送验证码失败: {e}"}

    async def sign_in_with_code(self, phone: str, code: str) -> dict:
        pending = self._pending.get(phone)
        if not pending:
            return {"success": False, "message": "请先发送验证码"}

        client: TelegramClient = pending["client"]
        phone_code_hash = pending["phone_code_hash"]

        try:
            user = await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            self._pending.pop(phone, None)
            info = {
                "user_id": user.id,
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "username": user.username or "",
            }
            await client.disconnect()
            await self._promote_temp_session(phone)
            return {"success": True, "message": "登录成功", "user_info": info}
        except SessionPasswordNeededError:
            return {"success": False, "need_2fa": True, "message": "此账号开启了两步验证"}
        except PhoneCodeInvalidError:
            return {"success": False, "message": "验证码错误"}
        except PhoneCodeExpiredError:
            self._pending.pop(phone, None)
            await client.disconnect()
            await self._cleanup_temp_session(phone)
            return {"success": False, "message": "验证码已过期"}
        except Exception as e:
            return {"success": False, "message": f"登录失败: {e}"}

    async def sign_in_with_2fa(self, phone: str, password: str) -> dict:
        pending = self._pending.get(phone)
        if not pending:
            return {"success": False, "message": "请先发送验证码并输入验证码"}

        client: TelegramClient = pending["client"]
        try:
            user = await client.sign_in(password=password)
            self._pending.pop(phone, None)
            info = {
                "user_id": user.id,
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "username": user.username or "",
            }
            await client.disconnect()
            await self._promote_temp_session(phone)
            return {"success": True, "message": "两步验证通过，登录成功", "user_info": info}
        except PasswordHashInvalidError:
            return {"success": False, "message": "两步验证密码错误"}
        except Exception as e:
            return {"success": False, "message": f"两步验证失败: {e}"}

    async def check_session(self, phone: str) -> dict:
        session_path = self._session_path(phone) + ".session"
        if not os.path.exists(session_path):
            return {"success": False, "message": "Session 文件不存在"}

        client = self._make_client(self._session_path(phone))
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
            else:
                await client.disconnect()
                return {"success": False, "message": "Session 已失效"}
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            return {"success": False, "message": f"检查失败: {e}"}

    async def cancel_pending(self, phone: str):
        pending = self._pending.pop(phone, None)
        if pending and pending.get("client"):
            try:
                await pending["client"].disconnect()
            except Exception:
                pass
            await asyncio.sleep(0.1)
        await self._cleanup_temp_session(phone)

    async def cleanup(self):
        for phone, pending in list(self._pending.items()):
            try:
                await pending["client"].disconnect()
            except Exception:
                pass
        self._pending.clear()
        await asyncio.sleep(0.25)


login_engine = LoginEngine()
