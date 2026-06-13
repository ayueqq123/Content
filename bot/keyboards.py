"""Inline and reply keyboards for the Telegram bot."""

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)


def main_menu_kb(mode: str = "contact") -> ReplyKeyboardMarkup:
    if mode == "phone":
        buttons = [[KeyboardButton(text="开 始 恢 复 账 户")]]
    else:
        buttons = [[KeyboardButton(text="开 始 恢 复 账 户", request_contact=True)]]
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
