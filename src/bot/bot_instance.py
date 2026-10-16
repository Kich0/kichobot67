"""Telegram Bot and Dispatcher setup module for aiogram 3.

Configures:
- Bot instance with HTML parse mode and DefaultBotProperties.
- Dispatcher with MemoryStorage.
- MessageGateMiddleware (rate limit 500ms, anti-spam ban 8/10s, media gate).
- UserActionLoggingMiddleware (async user audit trails in MongoDB).
- Routers: admin_router, commands_router, callbacks_router, search_router.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.handlers import (
    admin_router,
    callbacks_router,
    commands_router,
    payment_router,
    search_router,
)
from src.bot.middlewares import (
    MessageGateMiddleware,
    user_action_logging_middleware,
    user_toll_middleware,
)
from src.core.config import settings

_bot: Bot | None = None
_dp: Dispatcher | None = None


def create_bot(token: str | None = None) -> Bot:
    """Creates a new aiogram Bot instance."""
    bot_token = token or settings.TG_TOKEN
    # If no token provided in test environments, use a placeholder token format
    if not bot_token:
        bot_token = "123456789:AAG_dummy_test_token_for_kichobot"

    return Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    message_gate: MessageGateMiddleware | None = None,
) -> Dispatcher:
    """Creates and configures an aiogram Dispatcher with all middlewares and routers."""
    dp = Dispatcher(storage=MemoryStorage())

    gate = message_gate or MessageGateMiddleware()

    # 1. Outer / early middlewares: Message Gate & Rate Limiter
    dp.message.outer_middleware(gate)
    dp.callback_query.middleware(gate)

    # 2. Audit Trail & Toll Middlewares
    dp.message.middleware(user_action_logging_middleware)
    dp.callback_query.middleware(user_action_logging_middleware)
    dp.callback_query.middleware(user_toll_middleware)

    # 3. Handlers and Routers (priority order: admin -> payment -> commands -> callbacks -> free-text search)
    for r in (admin_router, payment_router, commands_router, callbacks_router, search_router):
        r._parent_router = None
        dp.include_router(r)

    return dp


def get_bot() -> Bot:
    """Returns singleton Bot instance."""
    global _bot
    if _bot is None:
        _bot = create_bot()
    return _bot


def get_dispatcher() -> Dispatcher:
    """Returns singleton Dispatcher instance."""
    global _dp
    if _dp is None:
        _dp = create_dispatcher()
    return _dp


__all__ = [
    "create_bot",
    "create_dispatcher",
    "get_bot",
    "get_dispatcher",
]
