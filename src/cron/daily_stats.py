"""Daily user activity and registration statistics logging cron job.

Runs at 23:59 (Asia/Almaty, UTC+5).
Logs daily counts into UserActivity and UserRegistrationStat MongoDB collections.
"""

from __future__ import annotations

from datetime import datetime, timezone
import zoneinfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.core.constants import TIMEZONE_NAME
from src.core.logging import get_logger

logger = get_logger("daily_stats")


def get_kz_start_of_day() -> datetime:
    """Returns UTC datetime representing start of current day in Asia/Almaty timezone."""
    tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
    now_kz = datetime.now(tz)
    start_kz = now_kz.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_kz.astimezone(timezone.utc)


async def log_daily_user_statistics() -> dict[str, int]:
    """Computes daily active users and registrations and stores snapshot in database."""
    logger.info("[DailyStats] 📊 Расчет ежедневной статистики пользователей (23:59 Asia/Almaty)...")
    start_utc = get_kz_start_of_day()
    start_naive = start_utc.replace(tzinfo=None)

    active_users_count = 0
    registered_users_count = 0

    try:
        from src.database.models.user import User
        from src.database.models.user_action import UserAction
        from src.database.models.user_activity import UserActivity
        from src.database.models.user_registration_stat import UserRegistrationStat

        # 1. Unique active users today from UserAction
        try:
            pipeline = [
                {
                    "$match": {
                        "$or": [
                            {"createdAt": {"$gte": start_utc}},
                            {"createdAt": {"$gte": start_naive}},
                        ]
                    }
                },
                {"$group": {"_id": "$userId"}},
            ]
            active_agg = await UserAction.aggregate(pipeline).to_list()
            active_users_count = len(active_agg)
        except Exception as e:
            logger.warning(f"[DailyStats] Ошибка подсчета активных пользователей: {e}")

        # 2. Registered users created today from User
        try:
            from beanie.operators import GTE, Or
            registered_users_count = await User.find(
                Or(
                    GTE(User.created_at, start_utc),
                    GTE(User.created_at, start_naive),
                )
            ).count()
        except Exception as e:
            logger.warning(f"[DailyStats] Ошибка подсчета регистраций пользователей: {e}")

        # 3. Persist snapshots
        activity_doc = UserActivity(userActivity=active_users_count)
        await activity_doc.insert()

        reg_doc = UserRegistrationStat(registeredUsers=registered_users_count)
        await reg_doc.insert()

        logger.info(
            f"[DailyStats] ✅ Статистика записана: активных пользователей={active_users_count}, "
            f"новых регистраций={registered_users_count}."
        )
    except Exception as e:
        logger.error(f"[DailyStats] Ошибка фиксации дневной статистики: {e}")

    return {
        "active_users": active_users_count,
        "registered_users": registered_users_count,
    }


def setup_daily_stats(scheduler: AsyncIOScheduler) -> None:
    """Registers 23:59 Asia/Almaty daily statistics logging cron job."""
    tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
    scheduler.add_job(
        log_daily_user_statistics,
        trigger=CronTrigger(hour=23, minute=59, timezone=tz),
        id="daily_user_statistics",
        name="Daily User Statistics (23:59 Asia/Almaty)",
        replace_existing=True,
    )
    logger.info("Registered daily user statistics cron at 23:59 Asia/Almaty.")
