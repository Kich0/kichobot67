"""Periodic process memory trimmer and garbage collector.

Invokes Python gc.collect() and glibc malloc_trim(0) on Linux (Render)
to immediately return unreferenced memory back to the operating system,
keeping RAM footprint low (~90-130 MB instead of 230+ MB).
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


def setup_memory_trim(scheduler: AsyncIOScheduler) -> None:
    """Schedules memory trimming every 30 minutes."""
    scheduler.add_job(
        trim_process_memory,
        "interval",
        minutes=30,
        id="memory_trim_job",
        replace_existing=True,
    )
    logger.info("Configured memory trim job (every 30 minutes)")


__all__ = [
    "setup_memory_trim",
    "trim_process_memory",
]
