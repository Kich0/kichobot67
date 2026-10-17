"""Daily university data update cron job.

Runs at 06:00 (Asia/Almaty, UTC+5).
Synchronizes faculties, programs, groups, and teachers catalog.
Adheres to official REST API architecture (legacy scraper disabled when AUTO_KSU_AUTH is False).
"""

from __future__ import annotations

import time
from typing import Any
import zoneinfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.core.config import settings
from src.core.constants import TIMEZONE_NAME
from src.core.logging import get_logger
from src.services.catalog_scanner import catalog_scanner_service
from src.services.teacher_directory import teacher_directory_service

logger = get_logger("daily_update")


async def run_weekly_catalog_sync() -> dict[str, Any]:
    """Runs weekly automated sync of newly formed university groups and teachers."""
    logger.info("[Sync] Запуск еженедельного сканирования каталога КарУ...")
    return await catalog_scanner_service.scan_and_sync()


async def run_daily_data_update(is_startup: bool = False, force_scan: bool = False) -> dict[str, Any]:
    """Executes daily catalog sync."""
    start_time = time.time()
    mode_label = "стартовое" if is_startup else "ежедневное"
    logger.info(f"[Sync] Начинаю {mode_label} обновление данных из КарУ.")

    try:
        from src.services.smart_search import smart_search_service

        smart_search_service.invalidate_caches()
        await smart_search_service.warmup()
    except Exception as e:
        logger.debug(f"[Sync] Прогрев smart_search_service пропущен: {e}")

    if not force_scan and not settings.AUTO_KSU_AUTH:
        logger.info(
            "[Sync] Пропуск устаревшего обновления данных через скрейпер КарГУ: "
            "AUTO_KSU_AUTH отключен. Бот работает через официальный REST API."
        )
        # Ensure teacher full directory in-memory profiles are loaded
        profiles_count = len(teacher_directory_service.teachers)
        logger.info(f"[Sync] Справочник преподавателей проверен: {profiles_count} записей в памяти.")
        return {"elapsed_seconds": round(time.time() - start_time, 2), "new_groups": 0, "new_teachers": 0}

    try:
        logger.info("[Sync] Проверка справочников КарУ через CatalogScannerService...")
        result = await run_weekly_catalog_sync()
        elapsed = round(time.time() - start_time, 2)
        logger.info(f"[Sync] Обновление данных КарУ завершено. Время: {elapsed} сек.")
        return result
    except Exception as e:
        logger.error(f"[Sync] Ошибка при обновлении данных: {e}")
        return {"elapsed_seconds": round(time.time() - start_time, 2), "new_groups": 0, "new_teachers": 0}


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


def setup_weekly_catalog_sync(scheduler: AsyncIOScheduler) -> None:
    """Registers Monday 04:00 Asia/Almaty weekly catalog sync cron job."""
    tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
    scheduler.add_job(
        run_weekly_catalog_sync,
        trigger=CronTrigger(day_of_week="mon", hour=4, minute=0, timezone=tz),
        id="weekly_catalog_sync",
        name="Weekly University Catalog Sync (Mon 04:00 Asia/Almaty)",
        replace_existing=True,
    )
    logger.info("Registered weekly catalog sync cron at Mon 04:00 Asia/Almaty.")

