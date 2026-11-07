"""Singleton Bot and Dispatcher of the invite bot.

Fully isolated from the main schedule bot: own token, own Dispatcher,
none of the main bot middlewares (message gate, action logger, toll).
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher

from src.core.config import settings
from src.invite_bot.handlers import router as join_router

INVITE_ALLOWED_UPDATES: list[str] = ["chat_member"]

_bot: Bot | None = None
_dp: Dispatcher | None = None


def get_invite_bot() -> Bot | None:
    """Returns the invite Bot, or None when the feature is disabled."""
    global _bot
    if not settings.invite_enabled:
        return None
    if _bot is None:
        _bot = Bot(token=settings.INVITE_BOT_TOKEN)
    return _bot


def get_invite_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        join_router._parent_router = None
        _dp.include_router(join_router)
    return _dp


async def close_invite_bot() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None


__all__ = ["INVITE_ALLOWED_UPDATES", "close_invite_bot", "get_invite_bot", "get_invite_dispatcher"]
