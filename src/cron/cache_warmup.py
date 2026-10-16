"""Intelligent Schedule Cache Warmup Engine & APScheduler Cron.

Manages continuous, pacing-controlled schedule pre-fetching:
- TOP-150 student groups cycled every 10 minutes (1 req / 4.0s = 15 req/min).
- TOP-30 teachers cycled every 5 minutes (1 req / 10.0s = 6 req/min).
- Combined background rate: 21 req/min (19% of 110 req/min limit, leaving 89 req/min free).
- Active window: 06:30 to 01:00 (Asia/Almaty, UTC+5).
- Night sleep: 01:00 to 06:30 (zero outbound network requests).
- Rate limiter headroom guard: skips tick if live user load reaches 105 req/min.
- Fire-and-forget background synchronization to MongoDB emergency fallback collections.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dt_time
import time
from typing import Any
import zoneinfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.core.constants import TIMEZONE_NAME
from src.core.logging import get_logger
from src.services.buketov_api import buketov_api_service

logger = get_logger("cache_warmup")


def is_warmup_active_kz(now_dt: datetime | None = None) -> bool:
    """Checks whether the current time in Asia/Almaty falls within the active warmup window.

    Active window: 06:30 to 01:00 (Asia/Almaty, UTC+5).
    Sleep window: 01:00 to 06:30 (night silence).
    """
    tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
    if now_dt is None:
        now_dt = datetime.now(tz)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=tz)
    else:
        now_dt = now_dt.astimezone(tz)

    current_time = now_dt.time()
    start_time = dt_time(6, 30)
    end_time = dt_time(1, 0)

    # Active across midnight: from 06:30:00 to 23:59:59 OR from 00:00:00 to 00:59:59
    return current_time >= start_time or current_time < end_time


class SmartWarmupEngine:
    """Continuous paced schedule warmup engine respecting API limits and RAM constraints."""

    # Pacing and sizing constants
    GROUP_INTERVAL_SEC: float = 4.0  # 150 groups over 600s (10 min) = 15 req/min
    TEACHER_INTERVAL_SEC: float = 10.0  # 30 teachers over 300s (5 min) = 6 req/min
    TOP_GROUPS_LIMIT: int = 150
    TOP_TEACHERS_LIMIT: int = 30
    TOP_LISTS_CACHE_TTL: float = 900.0  # 15 minutes
    HEADROOM: int = 5  # Leave at least 5 req/min margin under 110 limit

    SLEEP_CHECK_INTERVAL_SEC: float = 60.0
    PAUSE_CHECK_INTERVAL_SEC: float = 5.0
    RETRY_EMPTY_LIST_INTERVAL_SEC: float = 30.0

    def __init__(self) -> None:
        self._is_running: bool = False
        self._group_task: asyncio.Task[Any] | None = None
        self._teacher_task: asyncio.Task[Any] | None = None
        self._pause_until: float = 0.0
        self._background_tasks: set[asyncio.Task[Any]] = set()

        self._groups_cache: list[tuple[int, str]] = []
        self._groups_cache_updated_at: float = 0.0
        self._teachers_cache: list[tuple[int | str, str]] = []
        self._teachers_cache_updated_at: float = 0.0

    def _create_background_task(self, coro: Any) -> asyncio.Task[Any]:
        """Schedules a coroutine retaining a strong reference until completion to prevent GC drop."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    @property
    def is_running(self) -> bool:
        """Returns True if the engine is currently running."""
        return self._is_running

    def is_paused(self) -> bool:
        """Returns True if engine warmup is currently paused (e.g. by OOM guard)."""
        return time.time() < self._pause_until

    def pause(self, duration_seconds: float = 300.0) -> None:
        """Temporarily pauses schedule warmup requests for specified duration."""
        self._pause_until = max(self._pause_until, time.time() + duration_seconds)
        logger.warning(
            f"[SmartWarmupEngine] ⏸ Warmup paused for {duration_seconds}s "
            f"(until {datetime.fromtimestamp(self._pause_until).isoformat()})."
        )

    def resume(self) -> None:
        """Resumes schedule warmup immediately."""
        self._pause_until = 0.0
        logger.info("[SmartWarmupEngine] ▶️ Warmup resumed.")

    def set_top_groups(self, groups: list[tuple[int, str]]) -> None:
        """Sets top groups cache directly (useful for testing and bootstrapping)."""
        self._groups_cache = list(groups)
        self._groups_cache_updated_at = time.time()

    def set_top_teachers(self, teachers: list[tuple[int | str, str]]) -> None:
        """Sets top teachers cache directly (useful for testing and bootstrapping)."""
        self._teachers_cache = list(teachers)
        self._teachers_cache_updated_at = time.time()

    def start(self) -> None:
        """Starts background warmup workers if not already running."""
        if self._is_running:
            return
        self._is_running = True
        self._group_task = asyncio.create_task(self._group_worker(), name="warmup_groups")
        self._teacher_task = asyncio.create_task(self._teacher_worker(), name="warmup_teachers")
        logger.info(
            f"[SmartWarmupEngine] 🚀 Started warmup engine "
            f"(TOP-{self.TOP_GROUPS_LIMIT} groups @ {self.GROUP_INTERVAL_SEC}s, "
            f"TOP-{self.TOP_TEACHERS_LIMIT} teachers @ {self.TEACHER_INTERVAL_SEC}s)."
        )

    def stop(self) -> None:
        """Stops background warmup workers gracefully."""
        self._is_running = False
        for task in (self._group_task, self._teacher_task):
            if task and not task.done():
                task.cancel()
        for task in list(self._background_tasks):
            if task and not task.done():
                task.cancel()
        self._background_tasks.clear()
        self._group_task = None
        self._teacher_task = None
        logger.info("[SmartWarmupEngine] 🛑 Stopped warmup engine.")

    async def stop_async(self) -> None:
        """Stops background warmup workers and awaits task cancellation."""
        self._is_running = False
        tasks = [t for t in (self._group_task, self._teacher_task) if t and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._group_task = None
        self._teacher_task = None

        bg_tasks = [t for t in list(self._background_tasks) if t and not t.done()]
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)
        self._background_tasks.clear()
        logger.info("[SmartWarmupEngine] 🛑 Stopped warmup engine.")

    async def _get_top_groups(self) -> list[tuple[int, str]]:
        """Retrieves top popular student groups from cache or database."""
        now = time.time()
        if self._groups_cache and (now - self._groups_cache_updated_at < self.TOP_LISTS_CACHE_TTL):
            return self._groups_cache

        try:
            from src.database.models.user import User
            from src.database.models.group import Group

            top_group_ids: list[int] = []
            seen_ids: set[int] = set()

            def _add_gid(val: Any) -> None:
                if val is None:
                    return
                if isinstance(val, int):
                    gid = val
                elif isinstance(val, str) and val.strip().isdigit():
                    gid = int(val.strip())
                else:
                    return
                if gid > 0 and gid not in seen_ids and len(top_group_ids) < self.TOP_GROUPS_LIMIT:
                    seen_ids.add(gid)
                    top_group_ids.append(gid)

            try:
                pipeline = [
                    {"$match": {"group": {"$exists": True, "$ne": None}}},
                    {"$group": {"_id": "$group", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                    {"$limit": self.TOP_GROUPS_LIMIT},
                ]
                agg_result = await User.aggregate(pipeline).to_list()
                for item in agg_result:
                    _add_gid(item.get("_id"))
            except Exception as e:
                logger.debug(f"[SmartWarmupEngine] User aggregate for top groups failed: {e}")

            if len(top_group_ids) < self.TOP_GROUPS_LIMIT:
                try:
                    from src.database.models.user_action import UserAction

                    act_pipeline = [
                        {"$match": {"action": {"$regex": r"^(?:schedule|chooseScheduleLanguage)\|"}}} ,
                        {"$group": {"_id": "$action", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}},
                        {"$limit": self.TOP_GROUPS_LIMIT},
                    ]
                    act_res = await UserAction.aggregate(act_pipeline).to_list()
                    for item in act_res:
                        act_str = str(item.get("_id") or "")
                        parts = act_str.split("|")
                        if len(parts) > 2:
                            _add_gid(parts[2])
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] UserAction aggregate for top groups failed: {e}")

            if len(top_group_ids) < self.TOP_GROUPS_LIMIT:
                needed = self.TOP_GROUPS_LIMIT - len(top_group_ids)
                try:
                    from beanie.operators import NotIn

                    if top_group_ids:
                        extra_groups = await Group.find(NotIn(Group.group_id, top_group_ids)).limit(needed).to_list()
                    else:
                        extra_groups = await Group.find().limit(needed).to_list()
                    for g in extra_groups:
                        _add_gid(g.group_id)
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] Group fallback query failed: {e}")

            if top_group_ids:
                from beanie.operators import In

                groups = await Group.find(In(Group.group_id, top_group_ids)).to_list()
                group_map = {g.group_id: g.name for g in groups if g.name}
                res = [(gid, group_map[gid]) for gid in top_group_ids if gid in group_map]
                if res:
                    self._groups_cache = res
                    self._groups_cache_updated_at = now
                    return res
        except Exception as e:
            logger.warning(f"[SmartWarmupEngine] Error loading top groups: {e}")

        return self._groups_cache

    async def _get_top_teachers(self) -> list[tuple[int | str, str]]:
        """Retrieves top popular teachers from cache, database, or directory."""
        now = time.time()
        if self._teachers_cache and (now - self._teachers_cache_updated_at < self.TOP_LISTS_CACHE_TTL):
            return self._teachers_cache

        try:
            from src.database.models.user import User
            from src.database.models.teacher import Teacher

            top_teacher_ids: list[int | str] = []
            seen_tids: set[int | str] = set()

            def _add_tid(val: Any) -> None:
                if val is None:
                    return
                tid: int | str
                if isinstance(val, int):
                    tid = val
                elif isinstance(val, str):
                    s = val.strip()
                    if not s:
                        return
                    tid = int(s) if s.isdigit() else s
                else:
                    return
                if tid not in seen_tids and len(top_teacher_ids) < self.TOP_TEACHERS_LIMIT:
                    seen_tids.add(tid)
                    top_teacher_ids.append(tid)

            try:
                pipeline = [
                    {"$match": {"teacher": {"$exists": True, "$ne": None}}},
                    {"$group": {"_id": "$teacher", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                    {"$limit": self.TOP_TEACHERS_LIMIT},
                ]
                agg_result = await User.aggregate(pipeline).to_list()
                for item in agg_result:
                    _add_tid(item.get("_id"))
            except Exception as e:
                logger.debug(f"[SmartWarmupEngine] User aggregate for top teachers failed: {e}")

            if len(top_teacher_ids) < self.TOP_TEACHERS_LIMIT:
                try:
                    from src.database.models.user_action import UserAction

                    act_pipeline = [
                        {"$match": {"action": {"$regex": r"^(?:TeacherSchedule|teacherImg|teacherText)\|"}}},
                        {"$group": {"_id": "$action", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}},
                        {"$limit": self.TOP_TEACHERS_LIMIT},
                    ]
                    act_res = await UserAction.aggregate(act_pipeline).to_list()
                    for item in act_res:
                        act_str = str(item.get("_id") or "")
                        parts = act_str.split("|")
                        if len(parts) > 1:
                            _add_tid(parts[1])
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] UserAction aggregate for top teachers failed: {e}")

            if len(top_teacher_ids) < self.TOP_TEACHERS_LIMIT:
                needed = self.TOP_TEACHERS_LIMIT - len(top_teacher_ids)
                try:
                    from beanie.operators import NotIn

                    num_ids = [t for t in top_teacher_ids if isinstance(t, int)]
                    if num_ids:
                        extra_teachers = await Teacher.find(NotIn(Teacher.teacher_id, num_ids)).limit(needed).to_list()
                    else:
                        extra_teachers = await Teacher.find().limit(needed).to_list()
                    for t in extra_teachers:
                        _add_tid(t.teacher_id)
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] Teacher fallback query failed: {e}")

            teacher_map: dict[int | str, str] = {}
            if top_teacher_ids:
                try:
                    from beanie.operators import In

                    num_ids = [t for t in top_teacher_ids if isinstance(t, int)]
                    if num_ids:
                        teachers = await Teacher.find(In(Teacher.teacher_id, num_ids)).to_list()
                        for t in teachers:
                            name = t.name or t.full_name
                            if name:
                                teacher_map[t.teacher_id] = name
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] Teacher In query failed: {e}")

            # Supplement from teacher_directory_service if DB has fewer entries
            if len(teacher_map) < self.TOP_TEACHERS_LIMIT:
                try:
                    from src.services.teacher_directory import teacher_directory_service

                    for t_dict in teacher_directory_service.teachers:
                        if len(teacher_map) >= self.TOP_TEACHERS_LIMIT:
                            break
                        t_id = t_dict.get("id") or t_dict.get("teacherId") or t_dict.get("name")
                        t_name = t_dict.get("name") or t_dict.get("fullName")
                        if t_id and t_name and t_id not in teacher_map:
                            teacher_map[t_id] = t_name
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] Teacher directory fallback failed: {e}")

            res = list(teacher_map.items())[:self.TOP_TEACHERS_LIMIT]
            if res:
                self._teachers_cache = res
                self._teachers_cache_updated_at = now
                return res
        except Exception as e:
            logger.warning(f"[SmartWarmupEngine] Error loading top teachers: {e}")

        return self._teachers_cache

    async def _warm_group(self, group_id: int, group_name: str) -> None:
        """Fetches and warms schedule for a single group into RAM and DB fallback."""
        try:
            from src.bot.handlers.callbacks_router import (
                _bg_persist_student_schedule,
                student_schedule_cache,
            )
            from src.services.schedule_adapter import schedule_api_adapter

            raw_data = await buketov_api_service.get_group_schedule(group_name, force_refresh=True)
            records = (
                raw_data.get("records", [])
                if isinstance(raw_data, dict)
                else (raw_data if isinstance(raw_data, list) else [])
            )
            is_from_db = bool(raw_data.get("_fromDb", False)) if isinstance(raw_data, dict) else False

            # If fallback from DB is completely empty, preserve existing valid cache
            if is_from_db and not records:
                existing = student_schedule_cache.get(f"{group_id}_ru")
                if existing and existing.get("data"):
                    logger.debug(
                        f"[SmartWarmupEngine] Keeping existing cache for group {group_name}; DB returned empty."
                    )
                    return

            adapted_ru = schedule_api_adapter.adapt_group_schedule(records, "ru")
            adapted_kz = schedule_api_adapter.adapt_group_schedule(records, "kz")
            entry_ts = float(raw_data.get("_cacheTimestamp") or time.time()) if isinstance(raw_data, dict) else time.time()

            student_schedule_cache[f"{group_id}_ru"] = {
                "data": adapted_ru,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
            }
            student_schedule_cache[f"{group_id}_kz"] = {
                "data": adapted_kz,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
            }
            if not is_from_db and adapted_ru:
                self._create_background_task(_bg_persist_student_schedule(group_id, adapted_ru))
            logger.debug(f"[SmartWarmupEngine] Warmed schedule for group {group_name} (id={group_id})")
        except Exception as e:
            logger.warning(f"[SmartWarmupEngine] Error warming group {group_name} (id={group_id}): {e}")

    async def _warm_teacher(self, teacher_id: int | str, teacher_name: str) -> None:
        """Fetches and warms schedule for a single teacher into RAM and DB fallback."""
        try:
            from src.bot.handlers.callbacks_router import (
                _bg_persist_teacher_schedule,
                teacher_table_cache,
                teacher_text_cache,
            )
            from src.services.schedule_adapter import schedule_api_adapter

            raw_data = await buketov_api_service.get_teacher_schedule(teacher_name, force_refresh=True)
            records = (
                raw_data.get("records", [])
                if isinstance(raw_data, dict)
                else (raw_data if isinstance(raw_data, list) else [])
            )
            is_from_db = bool(raw_data.get("_fromDb", False)) if isinstance(raw_data, dict) else False

            # If fallback from DB is completely empty, preserve existing valid cache
            if is_from_db and not records:
                existing = teacher_text_cache.get(f"{teacher_id}_ru")
                if existing and existing.get("data"):
                    logger.debug(
                        f"[SmartWarmupEngine] Keeping existing cache for teacher {teacher_name}; DB returned empty."
                    )
                    return

            adapted_ru = schedule_api_adapter.adapt_teacher_schedule(records, "ru")
            adapted_kz = schedule_api_adapter.adapt_teacher_schedule(records, "kz")
            entry_ts = float(raw_data.get("_cacheTimestamp") or time.time()) if isinstance(raw_data, dict) else time.time()

            teacher_text_cache[f"{teacher_id}_ru"] = {
                "data": adapted_ru,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
            }
            teacher_text_cache[f"{teacher_id}_kz"] = {
                "data": adapted_kz,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
            }
            teacher_table_cache[str(teacher_id)] = {
                "data": adapted_ru,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
            }
            if not is_from_db and adapted_ru:
                self._create_background_task(_bg_persist_teacher_schedule(teacher_id, adapted_ru))
            logger.debug(f"[SmartWarmupEngine] Warmed schedule for teacher {teacher_name} (id={teacher_id})")
        except Exception as e:
            logger.warning(f"[SmartWarmupEngine] Error warming teacher {teacher_name} (id={teacher_id}): {e}")

    async def _group_worker(self) -> None:
        """Cyclic worker fetching TOP groups at GROUP_INTERVAL_SEC spacing."""
        logger.info(f"[SmartWarmupEngine] Started group warmup worker (interval={self.GROUP_INTERVAL_SEC}s).")
        cursor = 0
        while self._is_running:
            try:
                if not is_warmup_active_kz():
                    await asyncio.sleep(self.SLEEP_CHECK_INTERVAL_SEC)
                    continue

                if self.is_paused():
                    remaining = max(0.01, self._pause_until - time.time())
                    await asyncio.sleep(min(remaining, self.PAUSE_CHECK_INTERVAL_SEC))
                    continue

                from src.cron.memory_trim import check_and_enforce_memory_limit

                if check_and_enforce_memory_limit():
                    await asyncio.sleep(self.PAUSE_CHECK_INTERVAL_SEC)
                    continue

                groups = await self._get_top_groups()
                if not groups:
                    await asyncio.sleep(self.RETRY_EMPTY_LIST_INTERVAL_SEC)
                    continue

                group_id, group_name = groups[cursor % len(groups)]
                cursor = (cursor + 1) % len(groups)

                if buketov_api_service.can_make_api_call(headroom=self.HEADROOM):
                    await self._warm_group(group_id, group_name)
                else:
                    logger.debug(
                        f"[SmartWarmupEngine] Skipped group {group_name}: API headroom reached."
                    )

                await asyncio.sleep(self.GROUP_INTERVAL_SEC)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[SmartWarmupEngine] Error in group worker loop: {e}")
                await asyncio.sleep(self.PAUSE_CHECK_INTERVAL_SEC)

    async def _teacher_worker(self) -> None:
        """Cyclic worker fetching TOP teachers at TEACHER_INTERVAL_SEC spacing."""
        logger.info(f"[SmartWarmupEngine] Started teacher warmup worker (interval={self.TEACHER_INTERVAL_SEC}s).")
        cursor = 0
        while self._is_running:
            try:
                if not is_warmup_active_kz():
                    await asyncio.sleep(self.SLEEP_CHECK_INTERVAL_SEC)
                    continue

                if self.is_paused():
                    remaining = max(0.01, self._pause_until - time.time())
                    await asyncio.sleep(min(remaining, self.PAUSE_CHECK_INTERVAL_SEC))
                    continue

                from src.cron.memory_trim import check_and_enforce_memory_limit

                if check_and_enforce_memory_limit():
                    await asyncio.sleep(self.PAUSE_CHECK_INTERVAL_SEC)
                    continue

                teachers = await self._get_top_teachers()
                if not teachers:
                    await asyncio.sleep(self.RETRY_EMPTY_LIST_INTERVAL_SEC)
                    continue

                teacher_id, teacher_name = teachers[cursor % len(teachers)]
                cursor = (cursor + 1) % len(teachers)

                if buketov_api_service.can_make_api_call(headroom=self.HEADROOM):
                    await self._warm_teacher(teacher_id, teacher_name)
                else:
                    logger.debug(
                        f"[SmartWarmupEngine] Skipped teacher {teacher_name}: API headroom reached."
                    )

                await asyncio.sleep(self.TEACHER_INTERVAL_SEC)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[SmartWarmupEngine] Error in teacher worker loop: {e}")
                await asyncio.sleep(self.PAUSE_CHECK_INTERVAL_SEC)


# Singleton instance
smart_warmup_engine = SmartWarmupEngine()


async def warmup_top_schedules(limit: int = 30) -> int:
    """Warms up schedules for top student groups into in-memory LRU cache."""
    logger.info(f"[Warmup] 🌅 Запуск утреннего прогрева расписаний (TOP-{limit} групп)...")
    warmed_count = 0

    try:
        from src.database.models.user import User
        from src.database.models.group import Group
        from src.bot.handlers.callbacks_router import student_schedule_cache
        from src.services.schedule_adapter import schedule_api_adapter

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
            for item in agg_result:
                raw_gid = item.get("_id")
                if isinstance(raw_gid, int):
                    top_group_ids.append(raw_gid)
                elif isinstance(raw_gid, str) and raw_gid.isdigit():
                    top_group_ids.append(int(raw_gid))
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
            top_group_ids.extend([g.group_id for g in extra_groups if g.group_id is not None])

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
                # Pre-fetch and cache student schedule using canonical get_group_schedule key
                raw_data = await buketov_api_service.get_group_schedule(group_obj.name, force_refresh=True)
                records = (
                    raw_data.get("records", [])
                    if isinstance(raw_data, dict)
                    else (raw_data if isinstance(raw_data, list) else [])
                )
                is_from_db = bool(raw_data.get("_fromDb", False)) if isinstance(raw_data, dict) else False
                entry_ts = float(raw_data.get("_cacheTimestamp") or time.time()) if isinstance(raw_data, dict) else time.time()
                student_schedule_cache[f"{group_obj.group_id}_ru"] = {
                    "data": schedule_api_adapter.adapt_group_schedule(records, "ru"),
                    "timestamp": entry_ts,
                    "is_from_db": is_from_db,
                }
                student_schedule_cache[f"{group_obj.group_id}_kz"] = {
                    "data": schedule_api_adapter.adapt_group_schedule(records, "kz"),
                    "timestamp": entry_ts,
                    "is_from_db": is_from_db,
                }
                warmed_count += 1
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


__all__ = [
    "SmartWarmupEngine",
    "is_warmup_active_kz",
    "setup_cache_warmup",
    "smart_warmup_engine",
    "warmup_top_schedules",
]
