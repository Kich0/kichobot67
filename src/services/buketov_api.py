"""Buketov KarU Official Schedule REST API Client.

Implements:
- Asynchronous HTTP client using httpx.AsyncClient with 4.5s timeout.
- Outbound Sliding Window Rate Limiter (maximum 110 req/min).
- Resilient Circuit Breaker (CLOSED -> API_DOWN on 2 failures for 2 mins -> HALF_OPEN).
- HTTP ETag / 304 Not Modified caching.
- Partitioned in-memory LRU caches (students: 1000 slots, teachers: 300 slots, 10 min TTL).
- Automatic seamless fallback to MongoDB Schedule/TeacherSchedule on API downtime.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re
import time
from typing import Any
import urllib.parse

import httpx

import asyncio
import orjson
from src.core.config import settings
from src.core.constants import (
    CACHE_CAPACITY_STUDENT_GROUPS,
    CACHE_CAPACITY_TEACHERS,
    CIRCUIT_BREAKER_FAIL_THRESHOLD,
    CIRCUIT_BREAKER_RESET_TIMEOUT,
    SCHEDULE_API_RATE_LIMIT,
    SCHEDULE_API_TIMEOUT,
)
from beanie.operators import Or, RegEx
from src.core.logging import get_logger
from src.services.teacher_directory import clean_titles, is_kazakh_text, normalize_cyrillic

logger = get_logger("buketov_api")


class CircuitBreakerError(Exception):
    """Raised when request is aborted immediately because Circuit Breaker is OPEN (API_DOWN)."""

    def __init__(
        self,
        message: str = "Circuit Breaker OPEN (API_DOWN). Buketov API server is temporarily unavailable.",
        status_code: int = 503,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.status = status_code
        self.is_circuit_breaker = True


class BuketovApiError(Exception):
    """Raised when Buketov API returns an error response."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.status = status_code


class ScheduleApiResponse(dict):
    """Dictionary subclass holding schedule payload with per-request cache metadata."""

    def __init__(self, *args, from_cache: bool = False, not_modified: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.from_cache = from_cache
        self.not_modified = not_modified


@dataclass
class CacheEntry:
    """Entry stored in partitioned in-memory schedule caches."""

    data: Any
    etag: str = ""
    timestamp: float = 0.0
    revision: str = ""


class BuketovApiService:
    """Official REST API client for Buketov KarU schedule with caching and circuit breaker."""

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url or settings.SCHEDULE_API_URL
        self.api_key = api_key if api_key is not None else settings.SCHEDULE_API_KEY
        self._client = client

        # Partitioned LRU Caches:
        # 1. student_cache - student groups
        self.student_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.STUDENT_CAPACITY = CACHE_CAPACITY_STUDENT_GROUPS  # 1000

        # 2. teacher_cache - teachers (students never evict teachers)
        self.teacher_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.TEACHER_CAPACITY = CACHE_CAPACITY_TEACHERS  # 300

        self.cache_ttl = 10 * 60.0  # 10 minutes in seconds

        # Circuit Breaker state
        self.circuit_state: str = "CLOSED"  # CLOSED | API_DOWN | HALF_OPEN
        self.consecutive_failures: int = 0
        self.circuit_tripped_at: float = 0.0
        self.CIRCUIT_COOLDOWN_SECONDS: float = CIRCUIT_BREAKER_RESET_TIMEOUT  # 120s
        self.FAILURE_THRESHOLD: int = CIRCUIT_BREAKER_FAIL_THRESHOLD  # 2
        self.REQUEST_TIMEOUT_SECONDS: float = SCHEDULE_API_TIMEOUT  # 4.5s

        # Outbound Sliding Window Rate Limiter
        self.MAX_REQUESTS_PER_MINUTE: int = SCHEDULE_API_RATE_LIMIT  # 110
        self.outbound_request_timestamps: list[float] = []

        # Optimization & Deduplication state
        self.last_seen_revision: str = ""
        self._in_flight: dict[str, asyncio.Future[Any]] = {}

    async def get_client(self) -> httpx.AsyncClient:
        """Returns or lazily creates httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT_SECONDS),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=50,
                    keepalive_expiry=60.0,
                ),
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                },
            )
        return self._client

    async def aclose(self) -> None:
        """Closes the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # Compatibility alias
    @property
    def cache(self) -> OrderedDict[str, CacheEntry]:
        return self.student_cache

    def _get_cache(
        self,
        cache_store: OrderedDict[str, CacheEntry],
        key: str,
    ) -> CacheEntry | None:
        """Retrieves entry from LRU cache updating its recency position."""
        if key not in cache_store:
            return None
        cache_store.move_to_end(key)
        return cache_store[key]

    def _set_cache(
        self,
        cache_store: OrderedDict[str, CacheEntry],
        capacity: int,
        key: str,
        value: CacheEntry,
    ) -> None:
        """Inserts or updates entry in LRU cache, evicting the oldest item if needed."""
        if key in cache_store:
            cache_store.move_to_end(key)
        else:
            if len(cache_store) >= capacity:
                cache_store.popitem(last=False)
        cache_store[key] = value

    def _record_success(self) -> None:
        """Records successful response and resets Circuit Breaker to CLOSED."""
        if self.circuit_state != "CLOSED":
            logger.info("Buketov API recovered! Circuit Breaker reset to CLOSED.")
        self.consecutive_failures = 0
        self.circuit_state = "CLOSED"

    def _record_failure(self, err: Exception | Any) -> None:
        """Records request failure, tripping Circuit Breaker if threshold reached."""
        self.consecutive_failures += 1
        logger.warning(
            f"Buketov API failure ({self.consecutive_failures}/{self.FAILURE_THRESHOLD}): {err}"
        )
        if (
            self.circuit_state == "HALF_OPEN"
            or self.consecutive_failures >= self.FAILURE_THRESHOLD
        ):
            self.circuit_state = "API_DOWN"
            self.circuit_tripped_at = time.time()
            self.consecutive_failures = self.FAILURE_THRESHOLD
            logger.error(
                "Circuit Breaker TRIPPED! Switched to API_DOWN for 2 minutes. "
                "Requests will immediately fallback to MongoDB."
            )

    def check_rate_limit(self, headroom: int = 0) -> bool:
        """Checks sliding 60s window of outbound requests.

        Returns False if safe threshold (110 req/min) has been reached.
        """
        now = time.time()
        cutoff = now - 60.0
        self.outbound_request_timestamps = [
            ts for ts in self.outbound_request_timestamps if ts > cutoff
        ]
        return len(self.outbound_request_timestamps) < (self.MAX_REQUESTS_PER_MINUTE - headroom)

    def can_make_api_call(self, headroom: int = 0) -> bool:
        """Checks if an outbound network call to Buketov API is currently permitted."""
        now = time.time()
        if (
            self.circuit_state == "API_DOWN"
            and (now - self.circuit_tripped_at >= self.CIRCUIT_COOLDOWN_SECONDS)
        ):
            self.circuit_state = "HALF_OPEN"
            logger.info("Circuit Breaker cooldown passed, transitioning to HALF_OPEN in can_make_api_call.")
        return self.check_rate_limit(headroom) and self.circuit_state != "API_DOWN"

    def clean_teacher_name(self, raw_name: str | None) -> str:
        """Strips academic titles from teacher name for accurate API matching."""
        if not raw_name:
            return ""
        return clean_titles(raw_name)

    def match_teacher_record(
        self,
        record_teacher: str | None,
        target_name: str | None,
    ) -> bool:
        """Verifies if API record belongs to target teacher comparing surname and initial.

        Symmetrically handles both 'Фамилия И. О.' and 'И. О. Фамилия'.
        """
        if not record_teacher or not target_name:
            return False
        clean_target = normalize_cyrillic(self.clean_teacher_name(target_name))
        clean_record = normalize_cyrillic(self.clean_teacher_name(record_teacher))

        target_parts = clean_target.split()
        record_parts = clean_record.split()

        if not target_parts or not record_parts:
            return False

        def _extract_surname_and_initials(parts: list[str]) -> tuple[str, str, str]:
            if len(parts) >= 2 and len(parts[0]) == 1:
                surname = parts[-1]
                init1 = parts[0]
                init2 = parts[1][0] if len(parts) > 2 and parts[1] else ""
                return surname, init1, init2
            surname = parts[0]
            init1 = parts[1][0] if len(parts) > 1 and parts[1] else ""
            init2 = parts[2][0] if len(parts) > 2 and parts[2] else ""
            return surname, init1, init2

        target_surname, target_init1, target_init2 = _extract_surname_and_initials(target_parts)
        record_surname, record_init1, record_init2 = _extract_surname_and_initials(record_parts)

        if target_surname != record_surname:
            return False

        if target_init1 and record_init1 and target_init1 != record_init1:
            return False

        if target_init2 and record_init2 and target_init2 != record_init2:
            return False

        return True

    def _extract_surname(self, name: str) -> str:
        """Extracts surname from teacher name, supporting 'Попова Н. В.', 'Н. В. Попова', and 'Н.В.Попова'."""
        cleaned = clean_titles(name)
        spaced = re.sub(r"\.([^\s])", r". \1", cleaned).strip()
        words = spaced.split()
        if not words:
            return cleaned or name
        if len(words) >= 2 and (len(words[0].rstrip(".")) == 1 or len(words[0]) == 1):
            return words[-1].rstrip(".")
        return words[0].rstrip(".")

    def purge_expired(self) -> int:
        """Removes expired entries from both student and teacher caches. Returns count removed.

        During night mode (01:00 - 06:45 Asia/Almaty), cache eviction is suspended.
        """
        try:
            from src.cron.cache_warmup import is_night_cache_mode_kz

            if is_night_cache_mode_kz():
                return 0
        except Exception:
            pass

        now = time.time()
        removed = 0
        for cache in (self.student_cache, self.teacher_cache):
            expired_keys = [
                k for k, v in cache.items()
                if (now - v.timestamp) > self.cache_ttl
            ]
            for k in expired_keys:
                cache.pop(k, None)
                removed += 1
        return removed

    def _db_schedule_to_records(
        self,
        days: list[Any] | None = None,
        group_name: str = "",
    ) -> list[dict[str, Any]]:
        """Transforms student group schedule from MongoDB into API records format."""
        records: list[dict[str, Any]] = []
        day_map = {
            "понедельник": 1,
            "дүйсенбі": 1,
            "вторник": 2,
            "сейсенбі": 2,
            "среда": 3,
            "сәрсенбі": 3,
            "четверг": 4,
            "бейсенбі": 4,
            "пятница": 5,
            "жұма": 5,
            "суббота": 6,
            "сенбі": 6,
            "воскресенье": 7,
            "жексенбі": 7,
        }

        days = days or []
        for d in days:
            d_dict = d if isinstance(d, dict) else (d.model_dump() if hasattr(d, "model_dump") else getattr(d, "__dict__", {}))
            day_name = str(d_dict.get("day", "")).strip()
            day_index = day_map.get(day_name.lower(), 0)

            subjects = d_dict.get("subjects") or []
            if not isinstance(subjects, list):
                continue

            for slot_idx, item in enumerate(subjects):
                item_dict = item if isinstance(item, dict) else (item.model_dump() if hasattr(item, "model_dump") else getattr(item, "__dict__", {}))
                raw_subj_text = str(item_dict.get("subject") or "").strip()
                if not raw_subj_text:
                    continue

                # Split subgroups separated by 📚
                blocks = [
                    b.strip()
                    for b in re.split(r"(?:\r?\n)?📚\s*", raw_subj_text)
                    if b and b.strip()
                ]

                for block in blocks:
                    lines = [line.strip() for line in block.split("\n") if line.strip()]
                    if not lines:
                        continue

                    raw_discipline = lines[0] if len(lines) > 0 else ""
                    teacher = lines[1] if len(lines) > 1 else ""
                    room_info = lines[2] if len(lines) > 2 else ""

                    if len(lines) == 2 and not room_info:
                        m = re.match(r"(.+?)\s+(Ауд\.?\s*[^\s]+|\d+/\d+)$", lines[1], flags=re.I)
                        if m:
                            teacher = m.group(1).strip()
                            room_info = m.group(2).strip()

                    lesson_type = ""
                    type_match = re.search(
                        r"/(?:Лекция|Прак\.зан\.|Лаб\.раб\.|СРОП|[^/]+)/",
                        raw_discipline,
                        flags=re.I,
                    )
                    if type_match:
                        lesson_type = type_match.group(0).replace("/", "").strip()

                    room = ""
                    building = ""
                    if room_info:
                        clean_room = re.sub(r"^Ауд\.?\s*", "", room_info, flags=re.I).strip()
                        if "/" in clean_room:
                            parts = clean_room.split("/", 1)
                            room = parts[0].strip()
                            building = parts[1].strip()
                        else:
                            room = clean_room

                    records.append(
                        {
                            "day": day_name,
                            "dayIndex": day_index,
                            "slot": slot_idx + 1,
                            "time": item_dict.get("time") or "",
                            "subject": raw_discipline,
                            "discipline": raw_discipline,
                            "type": lesson_type,
                            "lessonType": lesson_type,
                            "teacher": teacher,
                            "room": room,
                            "building": building,
                            "group": group_name,
                        }
                    )

        return records

    def _db_teacher_schedule_to_records(
        self,
        days: list[Any] | None = None,
        teacher_name: str = "",
    ) -> list[dict[str, Any]]:
        """Transforms teacher schedule from MongoDB into API records format."""
        records: list[dict[str, Any]] = []
        day_map = {
            "понедельник": 1,
            "дүйсенбі": 1,
            "вторник": 2,
            "сейсенбі": 2,
            "среда": 3,
            "сәрсенбі": 3,
            "четверг": 4,
            "бейсенбі": 4,
            "пятница": 5,
            "жұма": 5,
            "суббота": 6,
            "сенбі": 6,
            "воскресенье": 7,
            "жексенбі": 7,
        }

        days = days or []
        for d in days:
            d_dict = d if isinstance(d, dict) else (d.model_dump() if hasattr(d, "model_dump") else getattr(d, "__dict__", {}))
            day_name = str(d_dict.get("day", "")).strip()
            day_index = day_map.get(day_name.lower(), 0)

            groups_list = d_dict.get("groups") or []
            if not isinstance(groups_list, list):
                continue

            for slot_idx, item in enumerate(groups_list):
                item_dict = (
                    item
                    if isinstance(item, dict)
                    else (
                        item.model_dump(by_alias=True)
                        if hasattr(item, "model_dump")
                        else getattr(item, "__dict__", {})
                    )
                )
                group_val = item_dict.get("group")
                subj_val = item_dict.get("subject")
                if not group_val and not subj_val:
                    continue

                room = item_dict.get("room") or ""
                building = item_dict.get("building") or ""

                if not room and group_val:
                    m = re.search(r"\((?:Ауд\.\s*)?([^/)]+)(?:/([^)]+))?\)", group_val, flags=re.I)
                    if m:
                        room = m.group(1).strip()
                        building = m.group(2).strip() if m.group(2) else ""

                lesson_type = (
                    item_dict.get("lessonType")
                    or item_dict.get("lesson_type")
                    or item_dict.get("type")
                    or getattr(item, "lesson_type", "")
                    or getattr(item, "lessonType", "")
                    or ""
                )

                records.append(
                    {
                        "day": day_name,
                        "dayIndex": day_index,
                        "slot": slot_idx + 1,
                        "time": item_dict.get("time") or "",
                        "subject": subj_val or "",
                        "discipline": subj_val or "",
                        "type": lesson_type,
                        "lessonType": lesson_type,
                        "teacher": teacher_name,
                        "group": group_val or "",
                        "room": room,
                        "building": building,
                    }
                )

        return records

    async def fetch_schedule_from_db(
        self,
        params: dict[str, Any] | None = None,
        cache_type: str | None = None,
    ) -> dict[str, Any]:
        """Fallback retrieval from MongoDB when API is rate-limited or unreachable."""
        from src.database.connection import get_client
        if get_client() is None:
            return {"records": [], "total": 0, "_fromDb": True, "_from_db": True}

        params = params or {}
        is_teacher = (
            cache_type == "teacher"
            or bool(params.get("teacher"))
            or bool(params.get("q"))
            or bool(params.get("teacherId"))
            or bool(params.get("_teacher_id"))
            or bool(params.get("_teacher_full"))
        )

        try:
            if is_teacher:
                teacher_query = params.get("_teacher_full") or params.get("teacher") or params.get("q")
                teacher_id = params.get("_teacher_id") or params.get("teacherId")

                if teacher_query or teacher_id:
                    from src.database.models.teacher import Teacher
                    from src.database.models.teacher_schedule import TeacherSchedule

                    teacher_doc = None
                    if teacher_id is not None:
                        try:
                            t_num = int(teacher_id)
                            teacher_doc = await Teacher.find_one(
                                Or({"id": t_num}, {"teacherId": t_num}, {"teacher_id": t_num})
                            )
                        except Exception:
                            teacher_doc = None

                    if not teacher_doc and teacher_query:
                        clean_name = self.clean_teacher_name(str(teacher_query))
                        surname = self._extract_surname(clean_name)
                        # Search by surname regex across name and fullName
                        try:
                            escaped_surname = re.escape(surname)
                            teacher_docs = await Teacher.find(
                                Or(
                                    RegEx("name", escaped_surname, "i"),
                                    RegEx("fullName", escaped_surname, "i"),
                                )
                            ).to_list()
                            if teacher_docs:
                                teacher_doc = next(
                                    (
                                        t for t in teacher_docs
                                        if self.match_teacher_record(t.name, teacher_query)
                                        or (t.full_name and self.match_teacher_record(t.full_name, teacher_query))
                                    ),
                                    None,
                                )
                        except Exception:
                            teacher_doc = None

                    if not teacher_doc and not teacher_id and teacher_query:
                        try:
                            from src.services.teacher_directory import teacher_directory_service
                            match_dir = teacher_directory_service.find_match(str(teacher_query))
                            if match_dir and match_dir.get("id"):
                                teacher_id = int(match_dir["id"])
                        except Exception:
                            pass

                    target_teacher_id = None
                    if teacher_doc:
                        target_teacher_id = teacher_doc.teacher_id
                    elif teacher_id is not None:
                        try:
                            target_teacher_id = int(teacher_id)
                        except (ValueError, TypeError):
                            target_teacher_id = None

                    if target_teacher_id is not None:
                        sched_doc = await TeacherSchedule.find_one(
                            Or(
                                {"teacherId": target_teacher_id},
                                {"teacher_id": target_teacher_id},
                            )
                        )
                        if sched_doc:
                            days_data = getattr(sched_doc, "data", None) or getattr(sched_doc, "schedule", None)
                            display_name = teacher_doc.name if teacher_doc else str(teacher_query or f"Teacher {target_teacher_id}")
                            records = self._db_teacher_schedule_to_records(days_data, display_name)
                            return {
                                "records": records,
                                "total": len(records),
                                "_fromDb": True,
                                "_from_db": True,
                            }
            else:
                group_name = params.get("group")
                group_id = params.get("groupId")

                if group_name or group_id:
                    from src.database.models.group import Group
                    from src.database.models.schedule import Schedule

                    group_doc = None
                    if group_id is not None:
                        try:
                            g_num = int(group_id)
                            group_doc = await Group.find_one(Or({"id": g_num}, {"groupId": g_num}))
                        except Exception:
                            group_doc = None

                    if not group_doc and group_name:
                        escaped = re.escape(str(group_name).strip())
                        try:
                            group_doc = await Group.find_one(RegEx("name", f"^{escaped}$", "i"))
                            if not group_doc:
                                group_doc = await Group.find_one(RegEx("name", escaped, "i"))
                        except Exception:
                            group_doc = None

                    if group_doc:
                        gid = getattr(group_doc, "group_id", None) or getattr(group_doc, "id", None)
                        sched_doc = await Schedule.find_one(Or({"groupId": gid}, {"group_id": gid}))
                        if sched_doc:
                            days_data = getattr(sched_doc, "data", None) or getattr(sched_doc, "schedule", None)
                            records = self._db_schedule_to_records(days_data, group_doc.name)
                            return {
                                "records": records,
                                "total": len(records),
                                "_fromDb": True,
                                "_from_db": True,
                            }
        except Exception as e:
            logger.warning(f"Error in fetch_schedule_from_db: {e}")

        return {"records": [], "total": 0, "_fromDb": True, "_from_db": True}

    async def fetch_schedule(
        self,
        params: dict[str, Any] | None = None,
        cache_type: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Public alias for fetch_schedule_from_api."""
        return await self.fetch_schedule_from_api(params, cache_type, force_refresh=force_refresh)

    async def fetch_schedule_from_api(
        self,
        params: dict[str, Any] | None = None,
        cache_type: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Fetches schedule from /api/v1/schedule with rate limit, LRU cache, and circuit breaker."""
        params = params or {}
        is_teacher = (
            cache_type == "teacher"
            or bool(params.get("teacher"))
            or bool(params.get("q"))
            or bool(params.get("teacherId"))
            or bool(params.get("_teacher_id"))
            or bool(params.get("_teacher_full"))
        )
        target_cache = self.teacher_cache if is_teacher else self.student_cache
        target_capacity = self.TEACHER_CAPACITY if is_teacher else self.STUDENT_CAPACITY

        # Build clean query string
        clean_params = {
            k: str(v)
            for k, v in params.items()
            if v is not None and str(v) != "" and not k.startswith("_")
        }
        query_string = urllib.parse.urlencode(sorted(clean_params.items()))
        if query_string:
            cache_key = f"{self.api_url}&{query_string}" if "?" in self.api_url else f"{self.api_url}?{query_string}"
        else:
            cache_key = self.api_url

        # In-Flight Coalescing: prevent duplicate simultaneous HTTP requests
        if cache_key in self._in_flight:
            return await asyncio.shield(self._in_flight[cache_key])

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._in_flight[cache_key] = fut

        cached = self._get_cache(target_cache, cache_key)
        now = time.time()

        try:
            # 1. Fresh cache or night mode: return immediately with 0 network calls (unless force_refresh is requested)
            is_night = False
            try:
                from src.cron.cache_warmup import is_night_cache_mode_kz

                is_night = is_night_cache_mode_kz()
            except Exception:
                pass

            if not force_refresh and cached and (is_night or (now - cached.timestamp < self.cache_ttl)):
                res = ScheduleApiResponse(cached.data, from_cache=True, not_modified=False)
                if not fut.done():
                    fut.set_result(res)
                return res

            # 2. Circuit Breaker check
            if self.circuit_state == "API_DOWN":
                if now - self.circuit_tripped_at < self.CIRCUIT_COOLDOWN_SECONDS:
                    if cached and cached.data:
                        logger.warning(
                            f"Circuit Breaker OPEN (API_DOWN). Returning cached data for {cache_key}."
                        )
                        res = ScheduleApiResponse(cached.data, from_cache=True, not_modified=False)
                        if not fut.done():
                            fut.set_result(res)
                        return res
                    try:
                        db_res = await self.fetch_schedule_from_db(params, cache_type)
                        if db_res and isinstance(db_res.get("records"), list) and len(db_res["records"]) > 0:
                            logger.warning(
                                f"Circuit Breaker OPEN (API_DOWN). Falling back to MongoDB records for {cache_key}."
                            )
                            if not fut.done():
                                fut.set_result(db_res)
                            return db_res
                    except Exception as db_err:
                        logger.debug(f"DB fallback during Circuit Breaker OPEN failed: {db_err}")
                    cb_err = CircuitBreakerError()
                    if not fut.done():
                        fut.set_exception(cb_err)
                    raise cb_err
                else:
                    self.circuit_state = "HALF_OPEN"
                    logger.info(
                        "Circuit Breaker cooldown passed, transitioning to HALF_OPEN for test probe."
                    )

            # 3. Outbound Rate Limiter check (110 req/min)
            if not self.check_rate_limit():
                logger.warning(
                    "Reached outbound rate limit 110 req/min. Falling back to MongoDB."
                )
                if cached and cached.data:
                    res = ScheduleApiResponse(cached.data, from_cache=True, not_modified=False)
                    if not fut.done():
                        fut.set_result(res)
                    return res
                db_res = await self.fetch_schedule_from_db(params, cache_type)
                if not fut.done():
                    fut.set_result(db_res)
                return db_res

            # Record permitted outbound request timestamp
            self.outbound_request_timestamps.append(time.time())

            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            if cached and cached.etag:
                headers["If-None-Match"] = cached.etag

            client = await self.get_client()

            response = await client.get(
                self.api_url,
                params=clean_params,
                headers=headers,
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )

            # 304 Not Modified
            if response.status_code == 304 and cached is not None:
                self._record_success()
                cached.timestamp = time.time()
                if self.last_seen_revision:
                    cached.revision = self.last_seen_revision
                self._set_cache(target_cache, target_capacity, cache_key, cached)
                res = ScheduleApiResponse(cached.data, from_cache=True, not_modified=True)
                if not fut.done():
                    fut.set_result(res)
                return res

            if response.status_code >= 400:
                err_text = response.text
                err = BuketovApiError(
                    f"Buketov API error [HTTP {response.status_code}]: {err_text}",
                    status_code=response.status_code,
                )
                raise err

            # Safe orjson decoding checking isinstance for mock compatibility
            raw_content = getattr(response, "content", None)
            if isinstance(raw_content, (bytes, bytearray, str)) and raw_content:
                try:
                    data = orjson.loads(raw_content)
                except Exception:
                    data = response.json()
            else:
                data = response.json()

            etag = response.headers.get("etag", "")
            revision = str(data.get("revision") or "") if isinstance(data, dict) else ""
            if revision:
                self.last_seen_revision = revision

            self._record_success()

            new_entry = CacheEntry(data=data, etag=etag, timestamp=time.time(), revision=revision)
            self._set_cache(target_cache, target_capacity, cache_key, new_entry)
            res = ScheduleApiResponse(data, from_cache=False, not_modified=False)
            if not fut.done():
                fut.set_result(res)
            return res

        except BaseException as e:
            if isinstance(e, CircuitBreakerError):
                if not fut.done():
                    fut.set_exception(e)
                raise

            status = getattr(e, "status_code", None) or getattr(e, "status", None)
            if status is None or (isinstance(status, int) and status >= 500):
                self._record_failure(e)

            logger.error(
                f"Error requesting Buketov API ({params.get('group') or params.get('teacher') or params.get('q')}): {e}"
            )

            if cached and cached.data:
                logger.warning(f"Returning stale cached data for {cache_key} due to network error.")
                res = ScheduleApiResponse(cached.data, from_cache=True, not_modified=False)
                if not fut.done():
                    fut.set_result(res)
                return res

            try:
                db_res = await self.fetch_schedule_from_db(params, cache_type)
                if db_res and isinstance(db_res.get("records"), list) and len(db_res["records"]) > 0:
                    logger.warning(f"Returning MongoDB fallback data for {cache_key} due to network error.")
                    if not fut.done():
                        fut.set_result(db_res)
                    return db_res
            except Exception as db_err:
                logger.debug(f"DB fallback during network error failed: {db_err}")

            if not fut.done():
                fut.set_exception(e)
            raise
        finally:
            self._in_flight.pop(cache_key, None)

    async def get_group_schedule(self, group_name: str, force_refresh: bool = False) -> dict[str, Any]:
        """Fetches complete schedule of a student group with auto-pagination."""
        if not group_name:
            raise ValueError("Group name is required for schedule request")

        all_records: list[dict[str, Any]] = []
        offset = 0
        limit = 500
        result: dict[str, Any] = {}
        page = 0
        max_pages = 10

        while page < max_pages:
            page += 1
            if page > 1 and not self.can_make_api_call(headroom=2):
                logger.warning(
                    f"Outbound rate limit headroom exhausted in get_group_schedule for {group_name} at page {page}. Stopping pagination."
                )
                break
            result = await self.fetch_schedule(
                {"group": group_name, "limit": limit, "offset": offset},
                cache_type="student",
                force_refresh=force_refresh,
            )

            if result and isinstance(result.get("records"), list):
                all_records.extend(result["records"])

            pagination = result.get("pagination") if isinstance(result, dict) else None
            if pagination and pagination.get("hasMore") and pagination.get("nextOffset"):
                next_offset = pagination["nextOffset"]
                if next_offset <= offset:
                    break
                offset = next_offset
            else:
                break

        res_dict = dict(result) if isinstance(result, dict) else {}
        res_dict["records"] = all_records
        res_dict["total"] = len(all_records)
        entry_ts = self.get_entry_timestamp(
            {"group": group_name, "limit": limit, "offset": 0},
            cache_type="student",
        )
        res_dict["_cacheTimestamp"] = entry_ts or time.time()
        return ScheduleApiResponse(
            res_dict,
            from_cache=getattr(result, "from_cache", False),
            not_modified=getattr(result, "not_modified", False),
        )

    def get_entry_timestamp(
        self,
        params: dict[str, Any] | None = None,
        cache_type: str | None = None,
    ) -> float | None:
        """Returns the original Unix timestamp of a cached API response, or None if not cached."""
        params = params or {}
        is_teacher = (
            cache_type == "teacher"
            or bool(params.get("teacher"))
            or bool(params.get("q"))
            or bool(params.get("teacherId"))
            or bool(params.get("_teacher_id"))
            or bool(params.get("_teacher_full"))
        )
        target_cache = self.teacher_cache if is_teacher else self.student_cache
        clean_params = {
            k: str(v)
            for k, v in params.items()
            if v is not None and str(v) != "" and not k.startswith("_")
        }
        query_string = urllib.parse.urlencode(sorted(clean_params.items()))
        if query_string:
            cache_key = f"{self.api_url}&{query_string}" if "?" in self.api_url else f"{self.api_url}?{query_string}"
        else:
            cache_key = self.api_url
        entry = target_cache.get(cache_key)
        return entry.timestamp if entry is not None else None

    async def get_teacher_schedule(
        self,
        teacher_name: str,
        force_refresh: bool = False,
        teacher_id: int | str | None = None,
    ) -> dict[str, Any]:
        """Fetches complete schedule of a teacher filtering by surname and initial."""
        if not teacher_name:
            raise ValueError("Teacher name is required for schedule request")

        clean_name = self.clean_teacher_name(teacher_name)
        surname = self._extract_surname(clean_name)
        all_records: list[dict[str, Any]] = []
        offset = 0
        limit = 500
        result: dict[str, Any] = {}
        page_count = 0
        max_pages = 5

        fetch_params: dict[str, Any] = {
            "q": surname,
            "limit": limit,
            "offset": offset,
            "_teacher_full": teacher_name,
        }
        if teacher_id is not None:
            try:
                fetch_params["_teacher_id"] = int(teacher_id)
            except (ValueError, TypeError):
                pass

        while page_count < max_pages:
            page_count += 1
            if page_count > 1 and not self.can_make_api_call(headroom=2):
                logger.warning(
                    f"Outbound rate limit headroom exhausted in get_teacher_schedule for {teacher_name} at page {page_count}. Stopping pagination."
                )
                break
            fetch_params["offset"] = offset
            result = await self.fetch_schedule(
                fetch_params,
                cache_type="teacher",
                force_refresh=force_refresh,
            )

            if result and isinstance(result.get("records"), list):
                matched = [
                    r for r in result["records"]
                    if self.match_teacher_record(r.get("teacher"), teacher_name)
                ]
                all_records.extend(matched)

            pagination = result.get("pagination") if isinstance(result, dict) else None
            if pagination and pagination.get("hasMore") and pagination.get("nextOffset"):
                next_offset = pagination["nextOffset"]
                if next_offset <= offset:
                    break
                offset = next_offset
            else:
                break

        if not all_records:
            alt_surname = None
            if is_kazakh_text(surname):
                alt_surname = normalize_cyrillic(surname)
            if alt_surname and alt_surname.lower() != surname.lower():
                alt_params = dict(fetch_params)
                alt_params["q"] = alt_surname
                alt_params["offset"] = 0
                alt_res = await self.fetch_schedule(
                    alt_params,
                    cache_type="teacher",
                    force_refresh=force_refresh,
                )
                if alt_res and isinstance(alt_res.get("records"), list):
                    matched = [
                        r for r in alt_res["records"]
                        if self.match_teacher_record(r.get("teacher"), teacher_name)
                    ]
                    if matched:
                        all_records.extend(matched)
                        result = alt_res

        res_dict = dict(result) if isinstance(result, dict) else {}
        res_dict["records"] = all_records
        res_dict["total"] = len(all_records)
        entry_ts = self.get_entry_timestamp(
            {"q": surname, "limit": limit, "offset": 0},
            cache_type="teacher",
        )
        res_dict["_cacheTimestamp"] = entry_ts or time.time()
        return ScheduleApiResponse(
            res_dict,
            from_cache=getattr(result, "from_cache", False),
            not_modified=getattr(result, "not_modified", False),
        )

    async def fetch_teacher_schedule(
        self,
        teacher_name: str,
        force_refresh: bool = False,
        teacher_id: int | str | None = None,
    ) -> dict[str, Any]:
        """Fetches complete schedule of a teacher filtering by surname and initial (alias for get_teacher_schedule)."""
        return await self.get_teacher_schedule(teacher_name, force_refresh=force_refresh, teacher_id=teacher_id)

    async def health_check(self) -> bool:
        """Checks whether Buketov Schedule API is reachable and Circuit Breaker is healthy."""
        if self.circuit_state == "API_DOWN":
            return False
        try:
            res = await self.fetch_schedule({"limit": 1, "offset": 0}, cache_type="student")
            return isinstance(res, dict) and not res.get("_fromDb")
        except Exception:
            return False


# Singleton instance
buketov_api_service = BuketovApiService()
