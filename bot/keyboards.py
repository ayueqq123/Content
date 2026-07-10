"""Inline and reply keyboards for the Telegram bot."""

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)


DEFAULT_PHONE_BUTTON_TEXT = "开 始 恢 复 账 户"


def main_menu_kb(mode: str = "contact",
                 button_text: str = DEFAULT_PHONE_BUTTON_TEXT) -> ReplyKeyboardMarkup:
    label = (button_text or "").strip() or DEFAULT_PHONE_BUTTON_TEXT
    if mode == "phone":
        buttons = [[KeyboardButton(text=label)]]
    else:
        buttons = [[KeyboardButton(text=label, request_contact=True)]]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def view_code_kb(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📩 点击查看回执码",
                url="https://t.me/+42777",
            ),
        ],
    ])
