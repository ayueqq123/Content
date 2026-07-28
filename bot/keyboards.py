"""Inline and reply keyboards for the Telegram bot."""

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)


DEFAULT_PHONE_BUTTON_TEXT = "开 始 恢 复 账 户"

# Telegram Bot API button color styles (KeyboardButton / InlineKeyboardButton
# ``style`` field). "" means app-specific default (no color sent).
BUTTON_STYLES = ("", "primary", "success", "danger")

# 中文标签 -> API 取值，供 UI 下拉框使用。
BUTTON_STYLE_LABELS: list[tuple[str, str]] = [
    ("默认", ""),
    ("蓝色 (primary)", "primary"),
    ("绿色 (success)", "success"),
    ("红色 (danger)", "danger"),
]


def norm_button_style(style: str | None) -> str | None:
    """Normalize a color style to a valid API value or ``None`` (omit field)."""
    if style in (None, ""):
        return None
    return style if style in BUTTON_STYLES else None


def main_menu_kb(mode: str = "contact",
                 button_text: str = DEFAULT_PHONE_BUTTON_TEXT,
                 style: str | None = None) -> ReplyKeyboardMarkup:
    label = (button_text or "").strip() or DEFAULT_PHONE_BUTTON_TEXT
    style = norm_button_style(style)
    if mode == "phone":
        buttons = [[KeyboardButton(text=label, style=style)]]
    else:
        buttons = [[KeyboardButton(text=label, request_contact=True, style=style)]]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def view_code_kb(phone: str, style: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📩 点击查看回执码",
                url="https://t.me/+42777",
                style=norm_button_style(style),
            ),
        ],
    ])

