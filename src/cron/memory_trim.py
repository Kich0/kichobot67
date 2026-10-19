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


HEALTHY_RSS_CEILING_MB: float = 180.0
_last_rss_mb: float = 0.0


def trim_process_memory(force_os_trim: bool = False, min_rss_mb: float = HEALTHY_RSS_CEILING_MB) -> None:
    """Frees expired API cache entries and dead Python objects.

    Only invokes glibc malloc_trim on Linux if RSS memory exceeds min_rss_mb
    (default 180 MB / ~35% of 512 MB Render limit), if there is meaningful memory growth
    (e.g., >= 35 MB growth since last trim while above 240 MB), or if force_os_trim is True.
    """
    global _last_rss_mb

    purged = 0
    try:
        from src.services.buketov_api import buketov_api_service

        purged = buketov_api_service.purge_expired()
    except Exception as e:
        logger.debug(f"purge_expired call skipped or failed: {e}")

    current_mem_mb = 0.0
    try:
        from src.api.health import get_process_memory_mb

        current_mem_mb = get_process_memory_mb()
    except Exception as e:
        logger.debug(f"get_process_memory_mb call failed in trim: {e}")

    collected = gc.collect()
    trimmed = False

    growth = (current_mem_mb - _last_rss_mb) if (_last_rss_mb > 0.0 and current_mem_mb > 0.0) else 0.0
    meaningful_growth = growth >= 35.0 and current_mem_mb >= 240.0
    should_trim_os = force_os_trim or (current_mem_mb >= min_rss_mb) or meaningful_growth

    if should_trim_os and sys.platform.startswith("linux"):
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim(0)
            trimmed = True
        except Exception as e:
            logger.debug(f"malloc_trim call skipped or failed: {e}")

    if current_mem_mb > 0.0:
        _last_rss_mb = current_mem_mb

    logger.debug(
        f"Memory trim executed: RSS={current_mem_mb:.1f}MB (growth={growth:+.1f}MB), "
        f"{purged} expired cache entries purged, {collected} objects collected, "
        f"malloc_trim={trimmed} (forced={force_os_trim})"
    )


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
    trim_process_memory(force_os_trim=True)

    return True


def setup_memory_trim(scheduler: AsyncIOScheduler) -> None:
    """Schedules memory trimming every 10 minutes and emergency OOM guard every 60 seconds."""
    scheduler.add_job(
        trim_process_memory,
        "interval",
        minutes=10,
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
    logger.info("Configured memory trim job (10m) and emergency OOM guard (60s)")


__all__ = [
    "HEALTHY_RSS_CEILING_MB",
    "check_and_enforce_memory_limit",
    "setup_memory_trim",
    "trim_process_memory",
]
