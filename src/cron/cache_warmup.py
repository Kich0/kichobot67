"""Intelligent Schedule Cache Warmup Engine & APScheduler Cron.

Manages continuous, pacing-controlled schedule pre-fetching:
- TOP-150 student groups cycled every 10 minutes (1 req / 4.0s = 15 req/min).
- TOP-60 teachers cycled every 5 minutes (1 req / 5.0s = 12 req/min).
- Combined background rate: 27 req/min (24.5% of 110 req/min limit, leaving 83 req/min free).
- Active window: 06:30 to 01:00 (Asia/Almaty, UTC+5).
- Night sleep: 01:00 to 06:30 (zero outbound network requests).
- Rate limiter headroom guard: skips tick if live user load reaches 105 req/min.
- Fire-and-forget background synchronization to MongoDB emergency fallback collections.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dt_time, timezone, timedelta
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
    TEACHER_INTERVAL_SEC: float = 5.0  # 60 teachers over 300s (5 min) = 12 req/min
    TOP_GROUPS_LIMIT: int = 150
    TOP_TEACHERS_LIMIT: int = 60
    TOP_LISTS_CACHE_TTL: float = 3600.0  # 1 hour (was 900.0)
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
        self._priority_teachers: dict[int | str, tuple[str, float]] = {}  # id -> (name, expires_at)
        self._priority_groups: dict[int, tuple[str, float]] = {}  # id -> (name, expires_at)
        self._group_cursor: int = 0
        self._teacher_cursor: int = 0

    def touch_group(self, group_id: int, group_name: str | None = None) -> None:
        """Dynamically enrolls or refreshes a student group in the priority warmup queue for 2 hours (7200s).

        Whenever a user views a group's schedule, this method is called to ensure
        the group is prioritized in cyclic 10-minute background warmup.
        """
        if group_id is None:
            return
        try:
            gid = int(group_id)
        except (ValueError, TypeError):
            return
        if gid <= 0:
            return

        g_name = (group_name or "").strip()
        if not g_name or (g_name.startswith("Группа ") and g_name.replace("Группа ", "").strip().isdigit()):
            try:
                from src.database.connection import get_client
                from src.bot.handlers.callbacks_router import _group_meta_cache

                meta = _group_meta_cache.get((id(get_client()), gid))
                if meta and meta.get("name") and not (meta["name"].startswith("Группа ") and meta["name"].replace("Группа ", "").strip().isdigit()):
                    g_name = meta["name"]
            except Exception:
                pass

        if not g_name or (g_name.startswith("Группа ") and g_name.replace("Группа ", "").strip().isdigit()):
            existing_p = self._priority_groups.get(gid)
            if existing_p and existing_p[0] and not (existing_p[0].startswith("Группа ") and existing_p[0].replace("Группа ", "").strip().isdigit()):
                g_name = existing_p[0]
            else:
                existing_c = next(
                    (
                        name for (i, name) in self._groups_cache
                        if i == gid and name and not (name.startswith("Группа ") and name.replace("Группа ", "").strip().isdigit())
                    ),
                    None,
                )
                if existing_c:
                    g_name = existing_c

        # If still no valid name resolved, do not poison priority warmup with a stub
        if not g_name or (g_name.startswith("Группа ") and g_name.replace("Группа ", "").strip().isdigit()):
            logger.debug(f"[SmartWarmupEngine] Skipping touch_group for gid={gid}: real name not resolved.")
            return

        now = time.time()
        expires_at = now + 7200.0  # 2 hours
        self._priority_groups[gid] = (g_name, expires_at)

        # Update in-memory cache: if not in cache, prepend it to priority queue,
        # but NEVER reset background rotation cursor to 0 (prevents starvation of downstream groups).
        existing_idx = next(
            (
                idx
                for idx, (i, name) in enumerate(self._groups_cache)
                if i == gid or (g_name and name and name.strip().lower() == g_name.strip().lower())
            ),
            None,
        )
        if existing_idx is not None:
            if g_name and self._groups_cache[existing_idx][1] != g_name:
                self._groups_cache[existing_idx] = (gid, g_name)
        else:
            self._groups_cache = [(gid, g_name)] + self._groups_cache[: self.TOP_GROUPS_LIMIT - 1]
            if self._group_cursor > 0:
                self._group_cursor += 1
        logger.debug(f"[SmartWarmupEngine] Touched group {g_name} (id={gid}) for priority warmup (2h).")

    def add_urgent_group(self, group_name: str, group_id: int | None = None) -> None:
        """Enrolls a group into priority warmup queue without resetting cursor."""
        if group_id is not None and group_id > 0:
            self.touch_group(group_id, group_name)
            return

        gid: int | None = None
        for cid, cname in self._groups_cache:
            if cname and group_name and cname.strip().lower() == group_name.strip().lower():
                gid = cid
                break
        if not gid:
            try:
                from src.services.smart_search import smart_search_service

                matches = smart_search_service.search_groups(group_name, limit=1)
                if matches and matches[0].get("id"):
                    gid = int(matches[0]["id"])
            except Exception:
                pass

        self.touch_group(gid if gid and gid > 0 else 0, group_name)

    def add_urgent_teacher(self, teacher_name: str, teacher_id: int | str | None = None) -> None:
        """Enrolls a teacher into priority warmup queue without resetting cursor."""
        self.touch_teacher(teacher_id or teacher_name, teacher_name)

    def touch_teacher(self, teacher_id: int | str, teacher_name: str | None = None) -> None:
        """Dynamically enrolls or refreshes a teacher in the priority warmup queue for 2 hours (7200s).

        Whenever a user views a teacher's schedule, this method is called to ensure
        the teacher is prioritized in cyclic background warmup for the next 2 hours.
        """
        if teacher_id is None or teacher_id == "":
            return

        norm_id: int | str
        if isinstance(teacher_id, int):
            norm_id = teacher_id
        elif isinstance(teacher_id, str) and teacher_id.strip().isdigit():
            norm_id = int(teacher_id.strip())
        else:
            norm_id = str(teacher_id).strip()
            if not norm_id:
                return

        t_name = (teacher_name or "").strip()
        if not t_name or str(t_name).isdigit() or str(t_name).startswith("ID "):
            try:
                from src.database.connection import get_client
                from src.bot.handlers.callbacks_router import _teacher_meta_cache

                meta = _teacher_meta_cache.get((id(get_client()), norm_id))
                if meta and (meta.get("lookup_name") or meta.get("name")):
                    cand_name = meta.get("lookup_name") or meta.get("name")
                    if cand_name and not str(cand_name).isdigit() and not str(cand_name).startswith("ID "):
                        t_name = cand_name
            except Exception:
                pass

        if not t_name or str(t_name).isdigit() or str(t_name).startswith("ID "):
            try:
                from src.services.teacher_directory import teacher_directory_service

                profile = teacher_directory_service.find_by_id(norm_id) if isinstance(norm_id, int) else None
                if profile:
                    t_name = profile.get("name") or profile.get("fullName") or ""
            except Exception:
                pass

        if not t_name or str(t_name).isdigit() or str(t_name).startswith("ID "):
            existing_p = self._priority_teachers.get(norm_id)
            if existing_p and existing_p[0] and not str(existing_p[0]).isdigit() and not str(existing_p[0]).startswith("ID "):
                t_name = existing_p[0]
            else:
                existing_c = next(
                    (
                        name for (tid, name) in self._teachers_cache
                        if tid == norm_id and name and not str(name).isdigit() and not str(name).startswith("ID ")
                    ),
                    None,
                )
                if existing_c:
                    t_name = existing_c

        # If still no valid name, do not poison priority queue with a bare ID stub
        if not t_name or str(t_name).isdigit() or str(t_name).startswith("ID "):
            logger.debug(f"[SmartWarmupEngine] Skipping touch_teacher for id={norm_id}: real name not resolved.")
            return

        now = time.time()
        expires_at = now + 7200.0  # 2 hours
        self._priority_teachers[norm_id] = (t_name, expires_at)

        # Update in-memory cache: if not in cache, prepend it to priority queue,
        # but NEVER reset background rotation cursor to 0 (prevents starvation of downstream teachers).
        existing_idx = next(
            (
                idx
                for idx, (tid, name) in enumerate(self._teachers_cache)
                if tid == norm_id
                or (t_name and name and name.strip().lower() == t_name.strip().lower())
            ),
            None,
        )
        if existing_idx is not None:
            if t_name and self._teachers_cache[existing_idx][1] != t_name:
                self._teachers_cache[existing_idx] = (norm_id, t_name)
        else:
            self._teachers_cache = [(norm_id, t_name)] + self._teachers_cache[: self.TOP_TEACHERS_LIMIT - 1]
            if self._teacher_cursor > 0:
                self._teacher_cursor += 1
        logger.debug(f"[SmartWarmupEngine] Touched teacher {t_name} (id={norm_id}) for priority warmup (2h).")

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
        self._group_cursor = 0

    def set_top_teachers(self, teachers: list[tuple[int | str, str]]) -> None:
        """Sets top teachers cache directly (useful for testing and bootstrapping)."""
        self._teachers_cache = list(teachers)
        self._teachers_cache_updated_at = time.time()
        self._teacher_cursor = 0

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
        # Clean expired priority groups
        self._priority_groups = {
            gid: (name, exp) for gid, (name, exp) in self._priority_groups.items() if exp > now
        }

        if self._groups_cache and (now - self._groups_cache_updated_at < self.TOP_LISTS_CACHE_TTL):
            active_p = [
                (gid, name)
                for gid, (name, exp) in sorted(self._priority_groups.items(), key=lambda x: x[1][1], reverse=True)
                if exp > now
            ]
            if active_p:
                p_ids = {p[0] for p in active_p}
                p_names = {p[1].strip().lower() for p in active_p if p[1]}
                merged = active_p + [
                    g for g in self._groups_cache
                    if g[0] not in p_ids and (g[1].strip().lower() not in p_names if g[1] else True)
                ]
                return merged[: self.TOP_GROUPS_LIMIT]
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

            # 1. Enqueue active priority groups (touched dynamically within the last 2 hours)
            active_p_sorted = sorted(self._priority_groups.items(), key=lambda x: x[1][1], reverse=True)
            for p_id, (p_name, _) in active_p_sorted:
                _add_gid(p_id)

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

                    cutoff_groups = datetime.now(timezone.utc) - timedelta(days=14)
                    act_pipeline = [
                        {
                            "$match": {
                                "createdAt": {"$gte": cutoff_groups},
                                "action": {"$regex": r"^(?:schedule|chooseScheduleLanguage)\|"},
                            }
                        },
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
                        extra_groups = await Group.find(NotIn("id", top_group_ids)).limit(needed).to_list()
                    else:
                        extra_groups = await Group.find().limit(needed).to_list()
                    for g in extra_groups:
                        _add_gid(getattr(g, "group_id", None) or getattr(g, "id", None))
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] Group fallback query failed: {e}")

            if top_group_ids:
                from beanie.operators import In

                groups = await Group.find(In("id", top_group_ids)).to_list()
                group_map = {(getattr(g, "group_id", None) or getattr(g, "id", None)): g.name for g in groups if g.name}
                for gid, (pname, _) in self._priority_groups.items():
                    if gid not in group_map and pname:
                        group_map[gid] = pname
                res = [(gid, group_map.get(gid, f"Группа {gid}")) for gid in top_group_ids]
                if res:
                    self._groups_cache = res
                    self._groups_cache_updated_at = now
                    return res
        except Exception as e:
            logger.warning(f"[SmartWarmupEngine] Error loading top groups: {e}")

        return self._groups_cache

    async def _get_top_teachers(self) -> list[tuple[int | str, str]]:
        """Retrieves top popular teachers from cache, database, or directory based on 45-day activity."""
        now = time.time()
        # Clean expired priority teachers
        self._priority_teachers = {
            tid: (name, exp) for tid, (name, exp) in self._priority_teachers.items() if exp > now
        }

        if self._teachers_cache and (now - self._teachers_cache_updated_at < self.TOP_LISTS_CACHE_TTL):
            active_p = [
                (tid, name)
                for tid, (name, exp) in sorted(self._priority_teachers.items(), key=lambda x: x[1][1], reverse=True)
                if exp > now
            ]
            if active_p:
                p_ids = {int(p[0]) if (isinstance(p[0], str) and p[0].isdigit()) else p[0] for p in active_p}
                p_names = {p[1].strip().lower() for p in active_p if p[1]}
                merged = active_p + [
                    t for t in self._teachers_cache
                    if (int(t[0]) if (isinstance(t[0], str) and str(t[0]).isdigit()) else t[0]) not in p_ids
                    and (t[1].strip().lower() not in p_names if t[1] else True)
                ]
                return merged[: self.TOP_TEACHERS_LIMIT]
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
                    if val <= 0:
                        return
                    tid = val
                elif isinstance(val, str):
                    s = val.strip()
                    if not s or s.lower() in ("none", "null", "0"):
                        return
                    tid = int(s) if s.isdigit() else s
                    if isinstance(tid, int) and tid <= 0:
                        return
                else:
                    return
                if tid not in seen_tids and len(top_teacher_ids) < self.TOP_TEACHERS_LIMIT * 2:
                    seen_tids.add(tid)
                    top_teacher_ids.append(tid)

            # 1. Enqueue active priority teachers (touched dynamically within the last 2 hours)
            active_p_sorted = sorted(self._priority_teachers.items(), key=lambda x: x[1][1], reverse=True)
            for p_id, (p_name, _) in active_p_sorted:
                _add_tid(p_id)

            # 2. Aggregate UserAction for activity in the last 45 days (1.5 months)
            if len(top_teacher_ids) < self.TOP_TEACHERS_LIMIT * 2:
                try:
                    from src.database.models.user_action import UserAction

                    cutoff_date = datetime.now(timezone.utc) - timedelta(days=45)

                    act_pipeline = [
                        {
                            "$match": {
                                "createdAt": {"$gte": cutoff_date},
                                "$or": [
                                    {
                                        "action": {
                                            "$in": [
                                                "view_teacher",
                                                "view_teacher_text",
                                                "view_teacher_image",
                                                "refresh_teacher",
                                                "refresh_teacher_image",
                                            ]
                                        }
                                    },
                                    {
                                        "action": {
                                            "$regex": r"^(?:TeacherSchedule|teacherImg|teacherText|refreshteacherImg|refresh_teacherimg|refreshTeacherSchedule)\|",
                                            "$options": "i",
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "$project": {
                                "teacher_target": {
                                    "$toString": {
                                        "$ifNull": [
                                            "$entityId",
                                            {
                                                "$ifNull": [
                                                    "$entityName",
                                                    {
                                                        "$arrayElemAt": [
                                                            {"$split": ["$action", "|"]},
                                                            1,
                                                        ]
                                                    },
                                                ]
                                            },
                                        ]
                                    }
                                }
                            }
                        },
                        {
                            "$match": {
                                "teacher_target": {"$ne": None, "$nin": ["", "0", "none", "null", "None", "Null"]}
                            }
                        },
                        {
                            "$group": {
                                "_id": "$teacher_target",
                                "count": {"$sum": 1},
                            }
                        },
                        {"$sort": {"count": -1}},
                        {"$limit": self.TOP_TEACHERS_LIMIT * 2},
                    ]
                    act_res = await UserAction.aggregate(act_pipeline).to_list()
                    for item in act_res:
                        raw_val = item.get("_id")
                        if raw_val is None:
                            continue
                        val_str = str(raw_val).strip()
                        if not val_str or val_str.lower() in ("none", "null", "0"):
                            continue
                        if "|" in val_str:
                            parts = val_str.split("|")
                            if len(parts) > 1 and parts[1].strip():
                                _add_tid(parts[1].strip())
                            else:
                                _add_tid(parts[0].strip())
                        else:
                            _add_tid(val_str)
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] UserAction aggregate for top teachers failed: {e}")

            # 3. Supplement from User preferences
            if len(top_teacher_ids) < self.TOP_TEACHERS_LIMIT:
                try:
                    pipeline = [
                        {"$match": {"teacher": {"$exists": True, "$ne": None, "$nin": [0, "0", ""]}}},
                        {"$group": {"_id": "$teacher", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}},
                        {"$limit": self.TOP_TEACHERS_LIMIT},
                    ]
                    agg_result = await User.aggregate(pipeline).to_list()
                    for item in agg_result:
                        _add_tid(item.get("_id"))
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] User aggregate for top teachers failed: {e}")

            # 4. Supplement from Teacher collection
            if len(top_teacher_ids) < self.TOP_TEACHERS_LIMIT:
                needed = self.TOP_TEACHERS_LIMIT - len(top_teacher_ids)
                try:
                    from beanie.operators import NotIn

                    num_ids = [t for t in top_teacher_ids if isinstance(t, int)]
                    if num_ids:
                        extra_teachers = await Teacher.find(NotIn("id", num_ids)).limit(needed).to_list()
                    else:
                        extra_teachers = await Teacher.find().limit(needed).to_list()
                    for t in extra_teachers:
                        _add_tid(getattr(t, "teacher_id", None) or getattr(t, "id", None))
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] Teacher fallback query failed: {e}")

            teacher_map: dict[int | str, str] = {}
            seen_teacher_identities: set[Any] = set()

            if top_teacher_ids:
                try:
                    from src.bot.handlers.callbacks_router import resolve_teacher_identity

                    for tid in top_teacher_ids:
                        if len(teacher_map) >= self.TOP_TEACHERS_LIMIT:
                            break
                        known_p_name = self._priority_teachers.get(tid, (None, 0))[0]
                        can_id, tch_name, dept_id, lookup_name = await resolve_teacher_identity(tid, known_p_name)
                        use_id = int(can_id) if (isinstance(can_id, str) and can_id.isdigit()) else can_id
                        use_name = lookup_name or tch_name or known_p_name

                        canonical_key = use_id if (use_id is not None and use_id != 0) else tid
                        if canonical_key in seen_teacher_identities:
                            continue

                        name_key = (use_name or "").strip().lower()
                        if name_key and name_key in seen_teacher_identities:
                            continue

                        seen_teacher_identities.add(canonical_key)
                        if name_key:
                            seen_teacher_identities.add(name_key)
                        if use_name:
                            teacher_map[canonical_key] = use_name
                        elif known_p_name:
                            teacher_map[canonical_key] = known_p_name
                        else:
                            teacher_map[canonical_key] = str(canonical_key)
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] Teacher resolution in _get_top_teachers failed: {e}")

            if len(teacher_map) < self.TOP_TEACHERS_LIMIT:
                needed = self.TOP_TEACHERS_LIMIT - len(teacher_map)
                try:
                    from beanie.operators import NotIn

                    num_ids = [t for t in teacher_map.keys() if isinstance(t, int)]
                    if num_ids:
                        extra_teachers = await Teacher.find(NotIn("id", num_ids)).limit(needed).to_list()
                    else:
                        extra_teachers = await Teacher.find().limit(needed).to_list()
                    for t in extra_teachers:
                        t_id = getattr(t, "teacher_id", None) or getattr(t, "id", None)
                        name = getattr(t, "name", None) or getattr(t, "full_name", None)
                        if t_id and t_id not in seen_teacher_identities and name:
                            name_key = name.strip().lower()
                            if name_key not in seen_teacher_identities:
                                seen_teacher_identities.add(t_id)
                                seen_teacher_identities.add(name_key)
                                teacher_map[t_id] = name
                except Exception as e:
                    logger.debug(f"[SmartWarmupEngine] Teacher fallback query failed: {e}")

            # Supplement from teacher_directory_service if DB has fewer entries
            if len(teacher_map) < self.TOP_TEACHERS_LIMIT:
                try:
                    from src.services.teacher_directory import teacher_directory_service

                    for t_dict in teacher_directory_service.teachers:
                        if len(teacher_map) >= self.TOP_TEACHERS_LIMIT:
                            break
                        t_name = t_dict.get("name") or t_dict.get("fullName")
                        if not t_name:
                            continue
                        name_key = t_name.strip().lower()
                        if name_key in seen_teacher_identities:
                            continue
                        t_id = t_dict.get("id") or t_dict.get("teacherId") or t_name
                        if t_id in seen_teacher_identities:
                            continue

                        seen_teacher_identities.add(name_key)
                        seen_teacher_identities.add(t_id)
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
                _has_any_lessons,
                student_schedule_cache,
            )
            from src.services.schedule_adapter import schedule_api_adapter

            # Resolve real group name if given stub or empty
            if not group_name or (group_name.startswith("Группа ") and group_name.replace("Группа ", "").strip().isdigit()):
                from src.bot.handlers.callbacks_router import _group_meta_cache
                from src.database.connection import get_client
                meta = _group_meta_cache.get((id(get_client()), group_id))
                if meta and meta.get("name"):
                    group_name = meta["name"]
                else:
                    try:
                        from src.database.models.group import Group

                        num_gid = int(group_id) if str(group_id).isdigit() else group_id
                        g_doc = await Group.find_one({"id": num_gid}) or await Group.find_one({"groupId": num_gid})
                        if g_doc and getattr(g_doc, "name", None):
                            group_name = g_doc.name
                            _group_meta_cache[(id(get_client()), group_id)] = {
                                "name": group_name,
                                "back_callback": "faculty|0",
                                "age": getattr(g_doc, "age", None),
                            }
                    except Exception:
                        pass

            if not group_name or (group_name.startswith("Группа ") and group_name.replace("Группа ", "").strip().isdigit()):
                logger.debug(f"[SmartWarmupEngine] Skipping warmup for group id={group_id}: name not resolved.")
                return

            # Skip redundant network fetch if already recently cached (< 3 min) and not from DB fallback
            existing_entry = student_schedule_cache.get(f"{group_id}_ru")
            entry_cached_ts = 0.0
            if existing_entry and isinstance(existing_entry, dict):
                try:
                    entry_cached_ts = float(existing_entry.get("timestamp") or 0.0)
                except (ValueError, TypeError):
                    entry_cached_ts = 0.0

            if (
                existing_entry
                and existing_entry.get("data")
                and not existing_entry.get("is_from_db")
                and entry_cached_ts > 0.0
                and (time.time() - entry_cached_ts < 180.0)
            ):
                logger.debug(
                    f"[SmartWarmupEngine] Skipping redundant warmup for group {group_name}; recently cached."
                )
                return

            from src.bot.handlers.callbacks_router import _is_college_group

            num_g_id = int(group_id) if str(group_id).isdigit() else group_id
            g_doc = None
            try:
                from src.database.models.group import Group

                g_doc = await Group.find_one({"id": num_g_id}) or await Group.find_one({"groupId": num_g_id})
            except Exception:
                g_doc = None

            fac_val = getattr(g_doc, "faculty", None) if g_doc else None
            is_perm_empty = _is_college_group(group_name, fac_val)
            if g_doc and (getattr(g_doc, "permanent_empty", None) is True or getattr(g_doc, "permanentEmpty", None) is True):
                is_perm_empty = True

            if is_perm_empty:
                logger.debug(
                    f"[SmartWarmupEngine] Skipping network warmup for permanently empty group {group_name}; recorded in DB."
                )
                for k in (f"{group_id}_ru", f"{group_id}_kz", f"{group_id}_рус", f"{group_id}_каз", f"{group_id}_қаз"):
                    student_schedule_cache.pop(k, None)
                from src.bot.handlers.callbacks_router import _bg_persist_empty_student_schedule

                self._create_background_task(_bg_persist_empty_student_schedule(group_id, is_permanent=True))
                return

            raw_data = await buketov_api_service.get_group_schedule(group_name, force_refresh=True)
            records = (
                raw_data.get("records", [])
                if isinstance(raw_data, dict)
                else (raw_data if isinstance(raw_data, list) else [])
            )
            is_from_db = bool(raw_data.get("_fromDb", False)) if isinstance(raw_data, dict) else False

            adapted_ru = schedule_api_adapter.adapt_group_schedule(records, "ru")
            adapted_kz = schedule_api_adapter.adapt_group_schedule(records, "kz")
            has_lessons = bool(records) and _has_any_lessons(adapted_ru)

            # If fallback from DB is completely empty or API returned 0 lessons, preserve existing valid cache
            if not has_lessons:
                existing = student_schedule_cache.get(f"{group_id}_ru")
                if existing and existing.get("data") and _has_any_lessons(existing["data"]):
                    logger.debug(
                        f"[SmartWarmupEngine] Keeping existing valid cache for group {group_name}; fetch returned 0 lessons."
                    )
                    return
                # Do NOT cache empty schedule in RAM
                for k in (f"{group_id}_ru", f"{group_id}_kz", f"{group_id}_рус", f"{group_id}_каз", f"{group_id}_қаз"):
                    student_schedule_cache.pop(k, None)
                from src.bot.handlers.callbacks_router import _bg_persist_empty_student_schedule

                self._create_background_task(_bg_persist_empty_student_schedule(group_id, is_permanent=is_perm_empty))
                return

            entry_ts = float(raw_data.get("_cacheTimestamp") or time.time()) if isinstance(raw_data, dict) else time.time()

            student_schedule_cache[f"{group_id}_ru"] = {
                "data": adapted_ru,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
                "is_from_cache": True,
            }
            student_schedule_cache[f"{group_id}_kz"] = {
                "data": adapted_kz,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
                "is_from_cache": True,
            }
            if not is_from_db and adapted_ru and has_lessons:
                self._create_background_task(_bg_persist_student_schedule(group_id, adapted_ru))
            logger.debug(f"[SmartWarmupEngine] Warmed schedule for group {group_name} (id={group_id})")
        except Exception as e:
            logger.warning(f"[SmartWarmupEngine] Error warming group {group_name} (id={group_id}): {e}")

    async def _warm_teacher(self, teacher_id: int | str, teacher_name: str) -> None:
        """Fetches and warms schedule for a single teacher into RAM and DB fallback with multi-key aliasing."""
        try:
            from src.bot.handlers.callbacks_router import (
                _bg_persist_teacher_schedule,
                _has_any_lessons,
                resolve_teacher_identity,
                teacher_text_cache,
            )
            from src.services.schedule_adapter import schedule_api_adapter
            from src.services.teacher_directory import clean_titles, format_initials, teacher_directory_service

            canonical_id, tch_name, dept_id, lookup_name = await resolve_teacher_identity(teacher_id, teacher_name)
            api_lookup = lookup_name or teacher_name

            # Skip redundant network fetch if already recently cached (< 2 min) and not from DB fallback
            existing_teacher = (
                (teacher_text_cache.get(f"{canonical_id}_ru") if canonical_id is not None else None)
                or (teacher_text_cache.get(f"{teacher_id}_ru") if teacher_id is not None else None)
            )
            t_cached_ts = 0.0
            if existing_teacher and isinstance(existing_teacher, dict):
                try:
                    t_cached_ts = float(existing_teacher.get("timestamp") or 0.0)
                except (ValueError, TypeError):
                    t_cached_ts = 0.0

            if (
                existing_teacher
                and existing_teacher.get("data")
                and not existing_teacher.get("is_from_db")
                and t_cached_ts > 0.0
                and (time.time() - t_cached_ts < 120.0)
            ):
                logger.debug(
                    f"[SmartWarmupEngine] Skipping redundant warmup for teacher {api_lookup}; recently cached."
                )
                return

            if not api_lookup or str(api_lookup).isdigit() or str(api_lookup).startswith("ID "):
                profile_cand = None
                if isinstance(canonical_id, int):
                    profile_cand = teacher_directory_service.find_by_id(canonical_id)
                if not profile_cand and isinstance(teacher_id, int):
                    profile_cand = teacher_directory_service.find_by_id(teacher_id)
                if not profile_cand and teacher_name:
                    profile_cand = teacher_directory_service.find_match(clean_titles(teacher_name))
                if profile_cand:
                    api_lookup = profile_cand.get("name") or profile_cand.get("fullName") or api_lookup

            raw_data = await buketov_api_service.get_teacher_schedule(api_lookup, force_refresh=True)
            records = (
                raw_data.get("records", [])
                if isinstance(raw_data, dict)
                else (raw_data if isinstance(raw_data, list) else [])
            )
            is_from_db = bool(raw_data.get("_fromDb", False)) if isinstance(raw_data, dict) else False

            adapted_ru = schedule_api_adapter.adapt_teacher_schedule(records, "ru")
            adapted_kz = schedule_api_adapter.adapt_teacher_schedule(records, "kz")
            has_lessons = bool(records) and _has_any_lessons(adapted_ru)

            # If fallback from DB is completely empty or fetch returned 0 lessons, preserve existing valid cache
            if not has_lessons:
                existing = (
                    (teacher_text_cache.get(f"{canonical_id}_ru") if canonical_id is not None else None)
                    or (teacher_text_cache.get(f"{teacher_id}_ru") if teacher_id is not None else None)
                )
                if existing and existing.get("data") and _has_any_lessons(existing["data"]):
                    logger.debug(
                        f"[SmartWarmupEngine] Keeping existing valid cache for teacher {api_lookup}; fetch returned 0 lessons."
                    )
                    return
                # Do NOT cache empty schedule in RAM
                for base in (canonical_id, teacher_id, tch_name, lookup_name):
                    if not base:
                        continue
                    for lang_sfx in ("_ru", "_kz", "_рус", "_каз", "_қаз"):
                        teacher_text_cache.pop(f"{base}{lang_sfx}", None)
                return

            entry_ts = float(raw_data.get("_cacheTimestamp") or time.time()) if isinstance(raw_data, dict) else time.time()

            cache_entry_ru = {
                "data": adapted_ru,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
                "is_from_cache": True,
            }
            cache_entry_kz = {
                "data": adapted_kz,
                "timestamp": entry_ts,
                "is_from_db": is_from_db,
                "is_from_cache": True,
            }

            # Multi-key alias caching: restrict to canonical keys to prevent TTLCache thrashing
            alias_ids: set[str] = set()

            def _add_alias(val: Any) -> None:
                if val is None:
                    return
                s = str(val).strip()
                if s and s not in alias_ids:
                    alias_ids.add(s)

            if canonical_id is not None:
                _add_alias(canonical_id)
            if teacher_id is not None:
                _add_alias(teacher_id)

            # Match in directory for canonical full name and short initials
            profile = None
            if isinstance(canonical_id, int):
                profile = teacher_directory_service.find_by_id(canonical_id)
            if not profile and isinstance(teacher_id, int):
                profile = teacher_directory_service.find_by_id(teacher_id)
            if not profile:
                for cand in (lookup_name, tch_name, teacher_name):
                    if cand:
                        profile = teacher_directory_service.find_match(clean_titles(cand))
                        if profile:
                            break

            if profile:
                _add_alias(profile.get("fullName"))
                _add_alias(profile.get("initials"))
                _add_alias(profile.get("name"))

            for cand in (tch_name, lookup_name, teacher_name):
                if cand:
                    cl = clean_titles(cand)
                    if cl:
                        _add_alias(cl)

            # Derive formatted and compact initials for string names
            for a_name in list(alias_ids):
                if not a_name.isdigit():
                    inits = format_initials(a_name)
                    if inits:
                        _add_alias(inits)
                        compact_inits = inits.replace(". ", ".")
                        if compact_inits != inits:
                            _add_alias(compact_inits)

            for a_id in alias_ids:
                if a_id:
                    teacher_text_cache[f"{a_id}_ru"] = cache_entry_ru
                    teacher_text_cache[f"{a_id}_kz"] = cache_entry_kz

            if not is_from_db and adapted_ru and has_lessons:
                self._create_background_task(_bg_persist_teacher_schedule(canonical_id, adapted_ru))
            logger.debug(f"[SmartWarmupEngine] Warmed schedule for teacher {api_lookup} (id={canonical_id})")
        except Exception as e:
            logger.warning(f"[SmartWarmupEngine] Error warming teacher {teacher_name} (id={teacher_id}): {e}")

    async def _group_worker(self) -> None:
        """Cyclic worker fetching TOP groups with drift-free pacing and no-skip retry."""
        logger.info(f"[SmartWarmupEngine] Started group warmup worker (interval={self.GROUP_INTERVAL_SEC}s).")
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

                self._group_cursor = self._group_cursor % len(groups)
                group_id, group_name = groups[self._group_cursor]

                # Check API headroom: DO NOT skip or advance cursor if headroom is reached!
                if not buketov_api_service.can_make_api_call(headroom=self.HEADROOM):
                    logger.debug(
                        f"[SmartWarmupEngine] Postponed group {group_name}: API headroom reached or API_DOWN."
                    )
                    await asyncio.sleep(min(self.GROUP_INTERVAL_SEC, 2.0))
                    continue

                start_mono = time.monotonic()
                await self._warm_group(group_id, group_name)
                self._group_cursor = (self._group_cursor + 1) % len(groups)

                elapsed = time.monotonic() - start_mono
                target_step = (
                    self.GROUP_INTERVAL_SEC
                    if self.GROUP_INTERVAL_SEC != 4.0
                    else max(2.0, 600.0 / max(1, len(groups)))
                )
                sleep_time = max(0.05, target_step - elapsed)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[SmartWarmupEngine] Error in group worker loop: {e}")
                await asyncio.sleep(self.PAUSE_CHECK_INTERVAL_SEC)

    async def _teacher_worker(self) -> None:
        """Cyclic worker fetching TOP teachers with drift-free pacing and no-skip retry."""
        logger.info(f"[SmartWarmupEngine] Started teacher warmup worker (interval={self.TEACHER_INTERVAL_SEC}s).")
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

                self._teacher_cursor = self._teacher_cursor % len(teachers)
                teacher_id, teacher_name = teachers[self._teacher_cursor]

                # Check API headroom: DO NOT skip or advance cursor if headroom is reached!
                if not buketov_api_service.can_make_api_call(headroom=self.HEADROOM):
                    logger.debug(
                        f"[SmartWarmupEngine] Postponed teacher {teacher_name}: API headroom reached or API_DOWN."
                    )
                    await asyncio.sleep(min(self.TEACHER_INTERVAL_SEC, 2.0))
                    continue

                start_mono = time.monotonic()
                await self._warm_teacher(teacher_id, teacher_name)
                self._teacher_cursor = (self._teacher_cursor + 1) % len(teachers)

                elapsed = time.monotonic() - start_mono
                target_step = (
                    self.TEACHER_INTERVAL_SEC
                    if self.TEACHER_INTERVAL_SEC != 5.0
                    else max(2.0, 300.0 / max(1, len(teachers)))
                )
                sleep_time = max(0.05, target_step - elapsed)
                await asyncio.sleep(sleep_time)

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
                extra_groups = await Group.find(NotIn("id", top_group_ids)).limit(needed).to_list()
            else:
                extra_groups = await Group.find().limit(needed).to_list()
            top_group_ids.extend([
                (getattr(g, "group_id", None) or getattr(g, "id", None))
                for g in extra_groups
                if (getattr(g, "group_id", None) or getattr(g, "id", None)) is not None
            ])

        if not top_group_ids:
            logger.info("[Warmup] Нет доступных групп для прогрева кэша.")
            return 0

        # Load Group details
        from beanie.operators import In

        groups = await Group.find(In("id", top_group_ids)).to_list()
        group_map = {(getattr(g, "group_id", None) or getattr(g, "id", None)): g for g in groups}

        for gid in top_group_ids:
            group_obj = group_map.get(gid)
            if not group_obj:
                continue

            try:
                # Check headroom to protect API limit when running alongside SmartWarmupEngine
                if not buketov_api_service.can_make_api_call(headroom=5):
                    logger.debug(f"[Warmup] Skipping group {group_obj.name}: API headroom reached or API down.")
                    continue

                # When SmartWarmupEngine is actively running, avoid force-refresh cache stampede
                force_val = not smart_warmup_engine.is_running
                raw_data = await buketov_api_service.get_group_schedule(group_obj.name, force_refresh=force_val)
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
                await asyncio.sleep(0.6)
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
