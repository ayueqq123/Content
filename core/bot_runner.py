"""Bot lifecycle manager — supports multiple bots running simultaneously.

When a token file is provided, each line is treated as a separate bot token
and all bots are started in parallel, sharing the same handler logic.
"""

import asyncio
import os
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import FSInputFile
from aiogram.client.session.aiohttp import AiohttpSession

from core.login_engine import login_engine, proxy_to_url
from core.config_manager import ConfigManager
from bot.handlers import router, check_timeouts_loop, set_config, set_chat_callback, get_chat_store

logger = logging.getLogger(__name__)

_dp = Dispatcher()
_dp.include_router(router)


class BotInstance:
    """Represents a single running bot."""

    def __init__(self, token: str, bot: Bot, label: str = ""):
        self.token = token
        self.bot = bot
        self.label = label  # e.g. @username or token[:8]
        self.polling_task: asyncio.Task | None = None
        self.timeout_task: asyncio.Task | None = None


class BotRunner:
    def __init__(self):
        self._instances: list[BotInstance] = []
        self._health_task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def bot(self) -> Bot | None:
        """Return the first bot instance (for chat panel compatibility)."""
        if self._instances:
            return self._instances[0].bot
        return None

    @property
    def bot_username(self) -> str:
        if self._instances:
            b = self._instances[0].bot
            if hasattr(b, "_me") and b._me:
                return f"@{b._me.username}"
        return ""

    @property
    def running_count(self) -> int:
        return len(self._instances)

    @property
    def bot_labels(self) -> list[str]:
        return [inst.label for inst in self._instances]

    @staticmethod
    def get_chat_store():
        return get_chat_store()

    @staticmethod
    def set_chat_callback(cb):
        set_chat_callback(cb)

    def _read_tokens(self, cfg: ConfigManager) -> list[str]:
        """Read tokens: single token from config, or multiple from file."""
        tokens = []
        token_file = cfg.get("token_file", "")
        if token_file and os.path.isfile(token_file):
            with open(token_file, "r", encoding="utf-8") as f:
                for line in f:
                    t = line.strip()
                    if t and not t.startswith("#"):
                        tokens.append(t)

        # Also include the main token if set
        main_token = cfg.get("bot_token", "")
        if main_token and main_token not in tokens:
            tokens.insert(0, main_token)

        return tokens

    async def start(self, cfg: ConfigManager) -> str:
        """Start bot(s). Returns empty string on success, error message on failure."""
        if self._running:
            return "Bot 已在运行"

        tokens = self._read_tokens(cfg)
        if not tokens:
            return "未配置 Bot Token（也未找到有效的 Token 文件）"

        api_id = int(cfg.get("api_id", 0))
        api_hash = cfg.get("api_hash", "")
        if api_id <= 0 or not api_hash:
            return "未配置 API_ID / API_HASH"

        proxy = cfg.get("proxy", "")
        sessions_dir = cfg.get("output_dir", "accounts")
        if not os.path.isabs(sessions_dir):
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            sessions_dir = os.path.join(base, sessions_dir)
        device_model = cfg.get("login_device_model", "Desktop Client")

        # Configure the shared login engine
        login_engine.configure(sessions_dir, proxy, device_model, api_id, api_hash)

        # Set remote server credentials
        from license.activator import LICENSE_SERVER_URL
        from license.hwid import get_hwid
        license_key = cfg.get("license_key", "")
        login_engine.set_server(LICENSE_SERVER_URL, license_key, get_hwid())

        # Give handlers access to config
        set_config(cfg)

        proxy_url = proxy_to_url(proxy)
        errors = []

        for i, token in enumerate(tokens):
            try:
                session = AiohttpSession(proxy=proxy_url) if proxy_url else None
                bot = Bot(token=token, session=session)

                # Validate token
                me = await bot.get_me()
                bot._me = me
                label = f"@{me.username}" if me.username else f"Bot#{i+1}"
                logger.info(f"Bot 已连接: {label}")

                try:
                    await bot.delete_webhook(drop_pending_updates=True)
                except Exception as e:
                    logger.warning(f"delete_webhook failed for {label}: {e}")

                # Auto-set bot name if configured (only for first bot)
                if i == 0:
                    bot_name = cfg.get("bot_name", "")
                    if bot_name:
                        try:
                            await bot.set_my_name(name=bot_name)
                            logger.info(f"{label} 昵称已设置: {bot_name}")
                        except Exception as e:
                            logger.warning(f"{label} 设置昵称失败: {e}")

                    # Auto-set bot avatar if configured
                    bot_avatar = cfg.get("bot_avatar", "")
                    if bot_avatar and os.path.isfile(bot_avatar):
                        try:
                            from aiogram.methods import SetChatPhoto
                            photo = FSInputFile(bot_avatar)
                            await bot(SetChatPhoto(chat_id=me.id, photo=photo))
                            logger.info(f"{label} 头像已设置: {bot_avatar}")
                        except Exception as e:
                            logger.warning(
                                f"{label} 设置头像失败 (可能需要通过 @BotFather 手动设置): {e}")

                inst = BotInstance(token=token, bot=bot, label=label)
                inst.polling_task = asyncio.create_task(self._run_polling(inst))
                inst.timeout_task = asyncio.create_task(check_timeouts_loop(bot))
                self._instances.append(inst)

            except Exception as e:
                err_msg = str(e)
                short_token = token[:8] + "..."
                if "unauthorized" in err_msg.lower():
                    errors.append(f"Token {short_token} 无效 (Unauthorized)")
                else:
                    errors.append(f"Token {short_token} 启动失败: {err_msg}")
                logger.error(f"Bot token {short_token} failed: {err_msg}")

        if not self._instances:
            return "所有 Bot 启动失败:\n" + "\n".join(errors)

        # Health check loop
        if cfg.get("health_check_enabled", False):
            interval = int(cfg.get("health_check_interval", 180))
            self._health_task = asyncio.create_task(
                self._health_check_loop(interval))

        self._running = True

        if errors:
            logger.warning(f"{len(errors)} 个 Bot 启动失败, {len(self._instances)} 个成功")
            return ""  # Partial success — don't block, already logged

        return ""

    async def _health_check_loop(self, interval: int):
        """Periodically check bot connectivity for all instances."""
        while True:
            try:
                await asyncio.sleep(interval)
                for inst in self._instances:
                    try:
                        me = await inst.bot.get_me()
                        logger.info(f"健康检测: {inst.label} 在线")
                    except Exception as e:
                        logger.error(f"健康检测失败 {inst.label}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康检测循环异常: {e}")

    async def _dispatch_update(self, inst: BotInstance, update):
        """Feed a single update to the dispatcher, isolating errors."""
        try:
            await _dp.feed_update(inst.bot, update)
        except Exception as e:
            logger.error(f"{inst.label} 处理消息异常: {e}", exc_info=True)

    async def _run_polling(self, inst: BotInstance):
        """Manual polling loop for one bot instance."""
        offset: int | None = None
        allowed = ["message", "callback_query", "edited_message"]
        logger.info(f"{inst.label} 开始轮询...")
        try:
            while True:
                try:
                    updates = await inst.bot.get_updates(
                        offset=offset, timeout=30, allowed_updates=allowed)
                    if updates:
                        logger.info(f"{inst.label} 收到 {len(updates)} 条消息")
                    for update in updates:
                        offset = update.update_id + 1
                        # Dispatch each update concurrently so a slow handler
                        # (e.g. waiting on the remote login server) does not
                        # block replies to other users.
                        asyncio.create_task(self._dispatch_update(inst, update))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    err = str(e).lower()
                    if "unauthorized" in err:
                        logger.error(f"{inst.label}: Token 已失效，停止轮询")
                        break
                    if "conflict" in err:
                        logger.warning(f"{inst.label}: Conflict (另一个实例在运行同一Token), 等待重试...")
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"{inst.label} 轮询错误: {e}")
                        await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass
        logger.info(f"{inst.label} 轮询已停止")

    async def stop(self):
        """Stop all running bots."""
        if not self._running and not self._instances:
            return

        # Stop health check
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
        self._health_task = None

        for inst in self._instances:
            if inst.timeout_task and not inst.timeout_task.done():
                inst.timeout_task.cancel()
                try:
                    await inst.timeout_task
                except (asyncio.CancelledError, Exception):
                    pass

            if inst.polling_task and not inst.polling_task.done():
                inst.polling_task.cancel()
                try:
                    await inst.polling_task
                except (asyncio.CancelledError, Exception):
                    pass

            try:
                await inst.bot.session.close()
            except Exception:
                pass

        self._instances.clear()
        await login_engine.cleanup()
        self._running = False
        logger.info("所有 Bot 已停止")


bot_runner = BotRunner()
