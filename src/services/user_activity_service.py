"""User activity and comprehensive statistics service.

Ports the comprehensive statistics calculation and formatting from Node.js
(commit 25601dc) with exact Asia/Almaty (UTC+5) day boundaries and aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.logging import get_logger

logger = get_logger("user_activity_service")

KZ_OFFSET = timezone(timedelta(hours=5))


@dataclass
class KZDayInfo:
    date_key: str  # "YYYY-MM-DD"
    day_month: str  # "DD.MM"
    start_utc: datetime
    end_utc: datetime


@dataclass
class DailyStatEntry:
    date_str: str  # "DD.MM"
    count: int = 0


@dataclass
class ComprehensiveStats:
    active_users_today: int = 0
    actions_today: int = 0
    registered_users_today: int = 0
    week_unique_users_count: int = 0
    week_total_actions_count: int = 0
    week_registered_users_count: int = 0
    daily_activity: list[DailyStatEntry] = field(default_factory=list)
    daily_actions: list[DailyStatEntry] = field(default_factory=list)
    daily_registrations: list[DailyStatEntry] = field(default_factory=list)


class UserActivityService:
    """Calculates bot online activity, actions, and registrations."""

    @staticmethod
    def get_kz_day_info(days_ago: int = 0) -> KZDayInfo:
        """Returns date keys and UTC timestamps for day boundary in Asia/Almaty (+05:00)."""
        now_utc = datetime.now(timezone.utc)
        now_kz = now_utc.astimezone(KZ_OFFSET)
        target_kz = now_kz - timedelta(days=days_ago)

        year = target_kz.year
        month = target_kz.month
        day = target_kz.day

        date_key = f"{year:04d}-{month:02d}-{day:02d}"
        day_month = f"{day:02d}.{month:02d}"

        # 00:00:00 Asia/Almaty converted to UTC
        start_kz = datetime(year, month, day, 0, 0, 0, tzinfo=KZ_OFFSET)
        start_utc = start_kz.astimezone(timezone.utc)
        end_utc = start_utc + timedelta(days=1, microseconds=-1)

        return KZDayInfo(
            date_key=date_key,
            day_month=day_month,
            start_utc=start_utc,
            end_utc=end_utc,
        )

    async def get_comprehensive_statistics(self) -> ComprehensiveStats:
        """Aggregates user online activity, actions, and registrations for past 7 days."""
        from src.database.models.user import User
        from src.database.models.user_action import UserAction

        # Flush enqueued actions before statistics calculation
        try:
            from src.bot.middlewares.user_action_logger import user_action_logging_middleware
            await user_action_logging_middleware.flush()
        except Exception:
            pass

        seven_days_ago = self.get_kz_day_info(7)
        seven_days_ago_start = seven_days_ago.start_utc

        action_map: dict[str, dict[str, Any]] = {}
        week_users_set: set[Any] = set()
        week_total_actions = 0

        # Try MongoDB aggregation pipeline
        aggregation_succeeded = False
        try:
            action_pipeline = [
                {"$match": {"createdAt": {"$gte": seven_days_ago_start}}},
                {
                    "$project": {
                        "userId": "$userId",
                        "dateKey": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$createdAt",
                                "timezone": "+05:00",
                            }
                        },
                    }
                },
                {
                    "$group": {
                        "_id": "$dateKey",
                        "actionsCount": {"$sum": 1},
                        "uniqueUsers": {"$addToSet": "$userId"},
                    }
                },
            ]
            action_agg = await UserAction.get_motor_collection().aggregate(action_pipeline).to_list(length=100)

            for item in action_agg:
                date_key = str(item.get("_id") or "")
                users = item.get("uniqueUsers") or []
                count = int(item.get("actionsCount") or 0)
                action_map[date_key] = {
                    "actionsCount": count,
                    "uniqueUsersCount": len(users),
                }
                for u in users:
                    week_users_set.add(u)
                week_total_actions += count

            aggregation_succeeded = True
        except Exception as e:
            logger.debug(f"Action aggregation fallback to in-memory: {e}")

        # In-memory fallback if aggregation failed or not supported in mock
        if not aggregation_succeeded:
            try:
                actions = await UserAction.find({"createdAt": {"$gte": seven_days_ago_start}}).to_list()
                for a in actions:
                    if not a.created_at:
                        continue
                    a_kz = a.created_at.astimezone(KZ_OFFSET)
                    date_key = f"{a_kz.year:04d}-{a_kz.month:02d}-{a_kz.day:02d}"
                    entry = action_map.setdefault(date_key, {"actionsCount": 0, "users": set()})
                    entry["actionsCount"] += 1
                    if a.user_id:
                        entry["users"].add(a.user_id)
                        week_users_set.add(a.user_id)
                    week_total_actions += 1

                for k, v in action_map.items():
                    if "users" in v:
                        v["uniqueUsersCount"] = len(v["users"])
            except Exception as e:
                logger.error(f"Error in in-memory action fallback: {e}")

        # User registrations aggregation
        reg_map: dict[str, int] = {}
        week_total_registered = 0
        reg_agg_succeeded = False
        try:
            user_pipeline = [
                {"$match": {"createdAt": {"$gte": seven_days_ago_start}}},
                {
                    "$project": {
                        "dateKey": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$createdAt",
                                "timezone": "+05:00",
                            }
                        }
                    }
                },
                {
                    "$group": {
                        "_id": "$dateKey",
                        "registeredCount": {"$sum": 1},
                    }
                },
            ]
            user_agg = await User.get_motor_collection().aggregate(user_pipeline).to_list(length=100)
            for item in user_agg:
                date_key = str(item.get("_id") or "")
                count = int(item.get("registeredCount") or 0)
                reg_map[date_key] = count
                week_total_registered += count
            reg_agg_succeeded = True
        except Exception as e:
            logger.debug(f"User registration aggregation fallback to in-memory: {e}")

        if not reg_agg_succeeded:
            try:
                users = await User.find({"createdAt": {"$gte": seven_days_ago_start}}).to_list()
                for u in users:
                    if not u.created_at:
                        continue
                    u_kz = u.created_at.astimezone(KZ_OFFSET)
                    date_key = f"{u_kz.year:04d}-{u_kz.month:02d}-{u_kz.day:02d}"
                    reg_map[date_key] = reg_map.get(date_key, 0) + 1
                    week_total_registered += 1
            except Exception as e:
                logger.error(f"Error in in-memory user registration fallback: {e}")

        # Today (days_ago = 0)
        today_info = self.get_kz_day_info(0)
        today_action_data = action_map.get(today_info.date_key, {"actionsCount": 0, "uniqueUsersCount": 0})
        active_users_today = today_action_data.get("uniqueUsersCount", 0)
        actions_today = today_action_data.get("actionsCount", 0)
        registered_users_today = reg_map.get(today_info.date_key, 0)

        # Past 7 days (days_ago = 1..7)
        daily_activity: list[DailyStatEntry] = []
        daily_actions: list[DailyStatEntry] = []
        daily_registrations: list[DailyStatEntry] = []

        for i in range(1, 8):
            day_info = self.get_kz_day_info(i)
            day_data = action_map.get(day_info.date_key, {"actionsCount": 0, "uniqueUsersCount": 0})
            reg_count = reg_map.get(day_info.date_key, 0)

            daily_activity.append(DailyStatEntry(date_str=day_info.day_month, count=day_data.get("uniqueUsersCount", 0)))
            daily_actions.append(DailyStatEntry(date_str=day_info.day_month, count=day_data.get("actionsCount", 0)))
            daily_registrations.append(DailyStatEntry(date_str=day_info.day_month, count=reg_count))

        return ComprehensiveStats(
            active_users_today=active_users_today,
            actions_today=actions_today,
            registered_users_today=registered_users_today,
            week_unique_users_count=len(week_users_set),
            week_total_actions_count=week_total_actions,
            week_registered_users_count=week_total_registered,
            daily_activity=daily_activity,
            daily_actions=daily_actions,
            daily_registrations=daily_registrations,
        )

    def format_stat_message(self, stats: ComprehensiveStats) -> str:
        """Formats comprehensive statistics into canonical legacy message representation."""
        lines: list[str] = []

        lines.append("Статистика ежедневного онлайна:")
        lines.append(f"Сегодня: {stats.active_users_today}")
        for item in stats.daily_activity:
            lines.append(f"{item.date_str}: {item.count}")
        lines.append(f"Всего уникальных пользователей за неделю: {stats.week_unique_users_count}\n")

        lines.append("Статистика ежедневных действий:")
        lines.append(f"Сегодня: {stats.actions_today}")
        for item in stats.daily_actions:
            lines.append(f"{item.date_str}: {item.count}")
        lines.append(f"Всего действий за неделю: {stats.week_total_actions_count}\n")

        lines.append("Статистика ежедневных регистраций:")
        lines.append(f"Сегодня: {stats.registered_users_today}")
        for item in stats.daily_registrations:
            lines.append(f"{item.date_str}: {item.count}")
        lines.append(f"Всего зареганных пользователей за неделю: {stats.week_registered_users_count}")

        return "\n".join(lines)


user_activity_service = UserActivityService()

__all__ = [
    "ComprehensiveStats",
    "DailyStatEntry",
    "KZDayInfo",
    "UserActivityService",
    "user_activity_service",
]
