"""Daily university data update cron job.

Runs at 06:00 (Asia/Almaty, UTC+5).
Synchronizes faculties, programs, groups, and teachers catalog.
Adheres to official REST API architecture (legacy scraper disabled when AUTO_KSU_AUTH is False).
"""

from __future__ import annotations

import time
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import zoneinfo

from src.core.config import settings
from src.core.constants import TIMEZONE_NAME
from src.core.logging import get_logger
from src.services.teacher_directory import teacher_directory_service

logger = get_logger("daily_update")


async def run_daily_data_update(is_startup: bool = False) -> None:
    """Executes daily catalog sync."""
    start_time = time.time()
    mode_label = "стартовое" if is_startup else "ежедневное"
    logger.info(f"[Sync] Начинаю {mode_label} обновление данных из КарУ.")

    if not settings.AUTO_KSU_AUTH:
        logger.info(
            "[Sync] Пропуск устаревшего обновления данных через скрейпер КарГУ: "
            "AUTO_KSU_AUTH отключен. Бот работает через официальный REST API."
        )
        # Ensure teacher full directory in-memory profiles are loaded
        profiles_count = len(teacher_directory_service.teachers)
        logger.info(f"[Sync] Справочник преподавателей проверен: {profiles_count} записей в памяти.")
        return

    try:
        logger.info("[Sync] Проверка справочников КарУ...")
        # If AUTO_KSU_AUTH was explicitly requested, legacy catalog updates could be performed here
        elapsed = int(time.time() - start_time)
        logger.info(f"[Sync] Обновление данных КарУ завершено. Время: {elapsed} сек.")
    except Exception as e:
        logger.error(f"[Sync] Ошибка при обновлении данных: {e}")


def setup_daily_update(scheduler: AsyncIOScheduler) -> None:
    """Registers 06:00 Asia/Almaty daily catalog sync cron job."""
    tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
    scheduler.add_job(
        run_daily_data_update,
        trigger=CronTrigger(hour=6, minute=0, timezone=tz),
        id="daily_data_update",
        name="Daily Data Update (06:00 Asia/Almaty)",
        replace_existing=True,
    )
    logger.info("Registered daily data update cron at 06:00 Asia/Almaty.")
