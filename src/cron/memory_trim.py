"""Periodic process memory trimmer and emergency OOM memory guard.

Invokes Python gc.collect() and glibc malloc_trim(0) on Linux (Render)
to immediately return unreferenced memory back to the operating system,
keeping RAM footprint low (~90-130 MB instead of 230+ MB).

Also includes active Emergency Memory Guard (threshold 430-450 MB)
to shed caches and pause warmup before Render's 512 MB OOM killer trips.
"""

from __future__ import annotations

import gc
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.logging import get_logger

logger = get_logger("memory_trim")


def trim_process_memory() -> None:
    """Frees dead Python objects and invokes glibc malloc_trim on Linux."""
    collected = gc.collect()
    trimmed = False

    if sys.platform.startswith("linux"):
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim(0)
            trimmed = True
        except Exception as e:
            logger.debug(f"malloc_trim call skipped or failed: {e}")

    logger.debug(f"Memory trim executed: {collected} objects collected, malloc_trim={trimmed}")


def check_and_enforce_memory_limit(threshold_mb: float = 430.0) -> bool:
    """Checks process RSS memory and performs emergency shedding if threshold reached.

    If memory >= threshold_mb (default 430.0 MB on Render's 512 MB limit):
    1. Clears in-memory schedule caches in callbacks_router (student, teacher text & table).
    2. Clears Buketov API service LRU caches (student and teacher).
    3. Pauses SmartWarmupEngine for 300 seconds (5 minutes).
    4. Performs gc.collect() and glibc malloc_trim(0) to release RAM back to OS.
    5. Returns True if emergency shed was triggered, False otherwise.
    """
    from src.api.health import get_process_memory_mb

    current_mem_mb = get_process_memory_mb()
    if current_mem_mb < threshold_mb:
        return False

    logger.warning(
        f"[OOM-GUARD] 🚨 Память процесса достигла {current_mem_mb:.1f} МБ (порог {threshold_mb:.1f} МБ)! "
        f"Произведен аварийный сброс всех кэшей ОЗУ."
    )

    # 1. Clear schedule caches in callbacks_router
    try:
        from src.bot.handlers.callbacks_router import clear_schedule_caches

        clear_schedule_caches()
    except Exception as e:
        logger.debug(f"[OOM-GUARD] Error clearing schedule caches: {e}")

    # 2. Clear Buketov API service caches
    try:
        from src.services.buketov_api import buketov_api_service

        buketov_api_service.student_cache.clear()
        buketov_api_service.teacher_cache.clear()
    except Exception as e:
        logger.debug(f"[OOM-GUARD] Error clearing Buketov API caches: {e}")

    # 3. Clear table image file_id cache
    try:
        from src.services.table_image import table_image_service

        table_image_service._file_id_cache.clear()
    except Exception as e:
        logger.debug(f"[OOM-GUARD] Error clearing table_image cache: {e}")

    # 4. Pause SmartWarmupEngine for 300 seconds (5 minutes)
    try:
        from src.cron.cache_warmup import smart_warmup_engine

        smart_warmup_engine.pause(300.0)
    except Exception as e:
        logger.debug(f"[OOM-GUARD] Error pausing smart_warmup_engine: {e}")

    # 5. Force GC and malloc_trim
    trim_process_memory()

    return True


def setup_memory_trim(scheduler: AsyncIOScheduler) -> None:
    """Schedules memory trimming every 30 minutes and emergency OOM guard every 60 seconds."""
    scheduler.add_job(
        trim_process_memory,
        "interval",
        minutes=30,
        id="memory_trim_job",
        replace_existing=True,
    )
    scheduler.add_job(
        check_and_enforce_memory_limit,
        "interval",
        seconds=60,
        id="memory_guard_job",
        replace_existing=True,
    )
    logger.info("Configured memory trim job (30m) and emergency OOM guard (60s)")


__all__ = [
    "check_and_enforce_memory_limit",
    "setup_memory_trim",
    "trim_process_memory",
]
