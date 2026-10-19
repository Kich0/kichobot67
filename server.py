"""UNIFIED SERVER — Kichobot (Python / FastAPI + aiogram 3).

Unified entry point for Kichobot:
- In 'webhook' mode: exports FastAPI `app` for Uvicorn (`uvicorn server:app`).
- In 'polling' mode: runs aiogram `dp.start_polling(bot)` for local development.
"""

from __future__ import annotations

import asyncio
import uvicorn

from src.api.app import app
from src.bot.bot_instance import get_bot, get_dispatcher
from src.core.config import settings
from src.core.logging import get_logger
from src.cron import (
    get_scheduler,
    setup_all_crons,
    start_scheduler,
    stop_scheduler,
)
from src.database.connection import close_db, init_db

logger = get_logger("server")


async def run_polling() -> None:
    """Runs bot in local polling mode."""
    logger.info("Starting Kichobot in POLLING mode...")
    await init_db()

    bot = get_bot()
    dp = get_dispatcher()

    # Bot commands setup
    try:
        from aiogram.types import BotCommand

        await bot.set_my_commands([
            BotCommand(command="start", description="Меню / Мәзір")
        ])
        await bot.set_my_description(description="Kicho — КарУ (Букетов) кестесі 📚")
        await bot.set_my_short_description(short_description="Kicho — КарУ (Букетов) кестесі 📚")
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")

    # Start APScheduler
    scheduler = get_scheduler()
    setup_all_crons(scheduler)
    start_scheduler()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Bot polling loop starting...")
        await dp.start_polling(bot)
    finally:
        stop_scheduler()
        try:
            from src.bot.middlewares.user_action_logger import user_action_logging_middleware
            await user_action_logging_middleware.flush()
        except Exception:
            pass
        await bot.session.close()
        await close_db()
        logger.info("Polling loop terminated cleanly.")


def main() -> None:
    """Command-line entry point."""
    if settings.BOT_MODE == "polling":
        asyncio.run(run_polling())
    else:
        logger.info(f"Starting Kichobot Webhook server on {settings.HOST}:{settings.PORT}...")
        try:
            from granian import Granian
            from granian.constants import Interfaces

            server = Granian(
                "server:app",
                address=settings.HOST,
                port=settings.PORT,
                interface=Interfaces.ASGI,
                workers=1,
                reload=settings.DEBUG,
                log_access=False,
            )
            server.serve()
        except ImportError:
            uvicorn.run(
                "server:app",
                host=settings.HOST,
                port=settings.PORT,
                reload=settings.DEBUG,
                access_log=False,
            )


if __name__ == "__main__":
    main()
