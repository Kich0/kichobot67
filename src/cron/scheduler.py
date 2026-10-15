"""APScheduler configuration and lifecycle management for Kichobot.

Runs recurring background jobs using AsyncIOScheduler with Asia/Almaty timezone.
"""

from __future__ import annotations

import zoneinfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.constants import TIMEZONE_NAME
from src.core.logging import get_logger

logger = get_logger("cron_scheduler")

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """Returns singleton AsyncIOScheduler instance configured with Asia/Almaty timezone."""
    global _scheduler
    if _scheduler is None:
        tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
        _scheduler = AsyncIOScheduler(timezone=tz)
    return _scheduler


def start_scheduler() -> None:
    """Starts the APScheduler if not already running."""
    scheduler = get_scheduler()
    if not scheduler.running:
        logger.info(f"Starting APScheduler ({TIMEZONE_NAME})...")
        scheduler.start()
        logger.info("APScheduler started successfully.")


def stop_scheduler() -> None:
    """Shuts down the APScheduler gracefully."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.info("Stopping APScheduler...")
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")
        _scheduler = None
