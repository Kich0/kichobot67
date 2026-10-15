"""Telegram bot handlers package."""

from src.bot.handlers.admin_router import admin_router
from src.bot.handlers.callbacks_router import callbacks_router
from src.bot.handlers.commands_router import commands_router
from src.bot.handlers.payment_router import payment_router
from src.bot.handlers.search_router import search_router

__all__ = [
    "admin_router",
    "callbacks_router",
    "commands_router",
    "payment_router",
    "search_router",
]

