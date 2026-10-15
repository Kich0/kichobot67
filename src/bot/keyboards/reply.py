"""Reply keyboard markup builders for Kichobot.

Implements the persistent bottom menu keyboard (commit 153 parity):
[ 🗒 Новое расписание ]  [ ❓ Помощь  ]
[ 🗓 Расписание преподавателя ]  [ 🗓 Расписание студента ]
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from src.core.i18n import t


def get_main_reply_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Returns persistent main reply keyboard matching Node.js commit 153."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=t("new_schedule", lang)),
                KeyboardButton(text=t("help", lang)),
            ],
            [
                KeyboardButton(text=t("teacher_schedule", lang)),
                KeyboardButton(text=t("student_schedule", lang)),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


__all__ = ["get_main_reply_keyboard"]
