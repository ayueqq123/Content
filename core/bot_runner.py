"""Bot lifecycle manager that runs aiogram polling in the Qt asyncio loop."""

import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from core.login_engine import login_engine, proxy_to_url
from core.config_manager import ConfigManager
from bot.handlers import router, check_timeouts_loop, set_config

logger = logging.getLogger(__name__)

_dp = Dispatcher()
_dp.include_router(router)


class BotRunner:
    def __init__(self):
        self._bot: Bot | None = None
        self._polling_task: asyncio.Task | None = None
        self._timeout_task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def bot_username(self) -> str:
        if self._bot and hasattr(self._bot, "_me") and self._bot._me:
            return f"@{self._bot._me.username}"
        return ""

    async def start(self, cfg: ConfigManager) -> str:
        """Start the bot. Returns empty string on success, error message on failure."""
        if self._running:
            return "Bot 已在运行"

        token = cfg.get("bot_token", "")
        if not token:
            # Try loading from token file
            token_file = cfg.get("token_file", "")
            if token_file and __import__("os").path.isfile(token_file):
                with open(token_file, "r") as f:
                    token = f.read().strip().split("\n")[0].strip()

        if not token:
            return "未配置 Bot Token"

        api_id = int(cfg.get("api_id", 0))
        api_hash = cfg.get("api_hash", "")
        if api_id <= 0 or not api_hash:
            return "未配置 API_ID / API_HASH"

        proxy = cfg.get("proxy", "")
        sessions_dir = cfg.get("output_dir", "accounts")
        import os
        if not os.path.isabs(sessions_dir):
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            sessions_dir = os.path.join(base, sessions_dir)
        device_model = cfg.get("login_device_model", "Desktop Client")

        # Configure the shared login engine
        login_engine.configure(sessions_dir, proxy, device_model, api_id, api_hash)

        # Set remote server credentials for the login engine
        from license.activator import LICENSE_SERVER_URL
        from license.hwid import get_hwid
        license_key = cfg.get("license_key", "")
        login_engine.set_server(LICENSE_SERVER_URL, license_key, get_hwid())

        # Give handlers access to config
        set_config(cfg)

        # Create bot with optional proxy
        try:
            proxy_url = proxy_to_url(proxy)
            session = AiohttpSession(proxy=proxy_url) if proxy_url else None
            self._bot = Bot(token=token, session=session)

            # Validate token by fetching bot info
            me = await self._bot.get_me()
            self._bot._me = me
            logger.info(f"Bot 已连接: @{me.username}")
        except Exception as e:
            self._bot = None
            err = str(e)
            if "unauthorized" in err.lower():
                return "Bot Token 无效 (Unauthorized)"
            return f"Bot 启动失败: {err}"

        try:
            await self._bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            logger.warning(f"delete_webhook failed: {e}")

        self._polling_task = asyncio.create_task(self._run_polling())
        self._timeout_task = asyncio.create_task(check_timeouts_loop(self._bot))
        self._running = True
        return ""

    async def _run_polling(self):
        feed_tasks: set[asyncio.Task] = set()
        offset: int | None = None
        try:
            while True:
                try:
                    updates = await self._bot.get_updates(offset=offset, timeout=30)
                    for update in updates:
                        offset = update.update_id + 1
                        task = asyncio.create_task(_dp.feed_update(self._bot, update))
                        feed_tasks.add(task)
                        task.add_done_callback(feed_tasks.discard)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    err = str(e).lower()
                    if "unauthorized" in err:
                        logger.error("Token 已失效，停止轮询")
                        break
                    if "conflict" in err:
                        logger.warning("Conflict: 其他实例在轮询，等待重试...")
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"Polling error: {e}")
                        await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass
        finally:
            for t in feed_tasks:
                t.cancel()
            if feed_tasks:
                await asyncio.gather(*feed_tasks, return_exceptions=True)
            self._running = False

    async def stop(self):
        if not self._running and not self._polling_task:
            return

        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
            try:
                await self._timeout_task
            except (asyncio.CancelledError, Exception):
                pass
        self._timeout_task = None

        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except (asyncio.CancelledError, Exception):
                pass
        self._polling_task = None

        if self._bot:
            try:
                await self._bot.session.close()
            except Exception:
                pass
            self._bot = None

        await login_engine.cleanup()
        self._running = False
        logger.info("Bot 已停止")


bot_runner = BotRunner()
