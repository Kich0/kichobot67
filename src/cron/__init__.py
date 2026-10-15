"""Cron jobs package for Kichobot.

Coordinates recurring background tasks:
- 06:00 (Asia/Almaty): Daily catalog update (faculties, programs, groups, teachers)
- 07:30 (Asia/Almaty): Morning cache warmup of TOP-30 groups
- 23:59 (Asia/Almaty): Daily user registration and activity statistics logging
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.cron.cache_warmup import setup_cache_warmup, warmup_top_schedules
from src.cron.daily_stats import get_kz_start_of_day, log_daily_user_statistics, setup_daily_stats
from src.cron.daily_update import run_daily_data_update, setup_daily_update
from src.cron.scheduler import get_scheduler, start_scheduler, stop_scheduler


def setup_all_crons(scheduler: AsyncIOScheduler | None = None) -> AsyncIOScheduler:
    """Configures and registers all production cron jobs on the given or default scheduler."""
    sched = scheduler or get_scheduler()
    setup_daily_update(sched)
    setup_cache_warmup(sched)
    setup_daily_stats(sched)
    return sched


__all__ = [
    "get_kz_start_of_day",
    "get_scheduler",
    "log_daily_user_statistics",
    "run_daily_data_update",
    "setup_all_crons",
    "setup_cache_warmup",
    "setup_daily_stats",
    "setup_daily_update",
    "start_scheduler",
    "stop_scheduler",
    "warmup_top_schedules",
]
