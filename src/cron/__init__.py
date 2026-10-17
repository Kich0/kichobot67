"""Cron jobs package for Kichobot.

Coordinates recurring background tasks:
- SmartWarmupEngine: continuous paced schedule warmup (TOP-150 groups, TOP-30 teachers)
- 06:00 (Asia/Almaty): Daily catalog update (faculties, programs, groups, teachers)
- 07:30 (Asia/Almaty): Morning cache warmup of TOP-30 groups
- 23:59 (Asia/Almaty): Daily user registration and activity statistics logging
- 60s & 30m: Process memory trim and emergency OOM guard
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.cron.cache_warmup import (
    SmartWarmupEngine,
    is_warmup_active_kz,
    setup_cache_warmup,
    smart_warmup_engine,
    warmup_top_schedules,
)
from src.cron.daily_stats import get_kz_start_of_day, log_daily_user_statistics, setup_daily_stats
from src.cron.daily_update import (
    run_daily_data_update,
    run_weekly_catalog_sync,
    setup_daily_update,
    setup_weekly_catalog_sync,
)
from src.cron.memory_trim import (
    check_and_enforce_memory_limit,
    setup_memory_trim,
    trim_process_memory,
)
from src.cron.scheduler import get_scheduler, start_scheduler, stop_scheduler


def setup_all_crons(scheduler: AsyncIOScheduler | None = None) -> AsyncIOScheduler:
    """Configures and registers all production cron jobs on the given or default scheduler."""
    sched = scheduler or get_scheduler()
    setup_daily_update(sched)
    setup_weekly_catalog_sync(sched)
    setup_cache_warmup(sched)
    setup_daily_stats(sched)
    setup_memory_trim(sched)
    return sched


__all__ = [
    "SmartWarmupEngine",
    "check_and_enforce_memory_limit",
    "get_kz_start_of_day",
    "get_scheduler",
    "is_warmup_active_kz",
    "log_daily_user_statistics",
    "run_daily_data_update",
    "run_weekly_catalog_sync",
    "setup_all_crons",
    "setup_cache_warmup",
    "setup_daily_stats",
    "setup_daily_update",
    "setup_memory_trim",
    "setup_weekly_catalog_sync",
    "smart_warmup_engine",
    "start_scheduler",
    "stop_scheduler",
    "trim_process_memory",
    "warmup_top_schedules",
]

