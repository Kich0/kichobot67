"""Morning schedule cache warmup cron job.

Runs at 07:30 (Asia/Almaty, UTC+5).
Pre-populates LRU RAM cache for TOP-30 student groups before peak morning requests.
"""

from __future__ import annotations

import asyncio
from typing import Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import zoneinfo

from src.core.constants import TIMEZONE_NAME
from src.core.logging import get_logger
from src.services.buketov_api import buketov_api_service

logger = get_logger("cache_warmup")


async def warmup_top_schedules(limit: int = 30) -> int:
    """Warms up schedules for top student groups into in-memory LRU cache."""
    logger.info(f"[Warmup] 🌅 Запуск утреннего прогрева расписаний (TOP-{limit} групп)...")
    warmed_count = 0

    try:
        from src.database.models.user import User
        from src.database.models.group import Group

        # Find most frequent groups among registered users
        top_group_ids: list[int] = []
        try:
            pipeline = [
                {"$match": {"group": {"$exists": True, "$ne": None}}},
                {"$group": {"_id": "$group", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": limit},
            ]
            agg_result = await User.aggregate(pipeline).to_list()
            top_group_ids = [item["_id"] for item in agg_result if item.get("_id")]
        except Exception as e:
            logger.debug(f"User aggregate for top groups failed: {e}")

        # Fallback to general Group collection if not enough groups found
        if len(top_group_ids) < limit:
            from beanie.operators import NotIn
            needed = limit - len(top_group_ids)
            if top_group_ids:
                extra_groups = await Group.find(NotIn(Group.group_id, top_group_ids)).limit(needed).to_list()
            else:
                extra_groups = await Group.find().limit(needed).to_list()
            top_group_ids.extend([g.group_id for g in extra_groups])

        if not top_group_ids:
            logger.info("[Warmup] Нет доступных групп для прогрева кэша.")
            return 0

        # Load Group details
        from beanie.operators import In
        groups = await Group.find(In(Group.group_id, top_group_ids)).to_list()
        group_map = {g.group_id: g for g in groups}

        for gid in top_group_ids:
            group_obj = group_map.get(gid)
            if not group_obj:
                continue

            try:
                # Pre-fetch and cache student schedule
                await buketov_api_service.fetch_schedule(
                    params={"groupId": group_obj.group_id, "group": group_obj.name},
                    cache_type="student",
                )
                warmed_count += 1
                # 150ms delay between fetches to respect university 110 req/min rate limit
                await asyncio.sleep(0.15)
            except Exception as e:
                logger.warning(f"[Warmup] Ошибка прогрева для группы {group_obj.name}: {e}")

        logger.info(f"[Warmup] ✅ Утренний прогрев завершен. Успешно загружено: {warmed_count} групп.")
    except Exception as e:
        logger.error(f"[Warmup] Критическая ошибка при утреннем прогреве: {e}")

    return warmed_count


def setup_cache_warmup(scheduler: AsyncIOScheduler) -> None:
    """Registers 07:30 Asia/Almaty morning cache warmup cron job."""
    tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
    scheduler.add_job(
        warmup_top_schedules,
        trigger=CronTrigger(hour=7, minute=30, timezone=tz),
        id="cache_warmup",
        name="Cache Warmup (07:30 Asia/Almaty)",
        replace_existing=True,
    )
    logger.info("Registered morning cache warmup cron at 07:30 Asia/Almaty.")
