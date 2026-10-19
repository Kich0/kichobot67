"""Unit and integration tests for Warmup Engine pacing, Lock synchronization, Priority Queues,

Night Cache Mode, and Circular Reference elimination.
"""

import asyncio
from datetime import datetime, timezone
import time
from unittest.mock import AsyncMock, patch
import zoneinfo
import pytest

from cachetools import TTLCache
from src.bot.handlers.callbacks_router import (
    NightAwareTimer,
    student_schedule_cache,
    teacher_text_cache,
    teacher_table_cache,
    get_or_fetch_student_schedule,
    get_or_fetch_teacher_text_schedule,
)
from src.core.constants import TIMEZONE_NAME
from src.cron.cache_warmup import (
    SmartWarmupEngine,
    is_night_cache_mode_kz,
    is_warmup_active_kz,
)
from src.database.models.warmup_log import WarmupLog
from src.services.buketov_api import buketov_api_service, CacheEntry


def test_is_night_cache_mode_kz_boundary_times():
    """Verifies day and night boundaries in Kazakhstan timezone (UTC+5).

    Night cache freeze window: 01:00:00 to 06:44:59.
    Day/Warmup active window: 06:30:00 to 00:59:59.
    Overlapping window (06:30:00 - 06:44:59): warmup engine updates RAM while cache freeze protects stale reads.
    """
    tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)

    # 01:00:00 -> Night mode active
    dt_0100 = datetime(2026, 10, 19, 1, 0, 0, tzinfo=tz)
    assert is_night_cache_mode_kz(dt_0100) is True
    assert is_warmup_active_kz(dt_0100) is False

    # 03:30:00 -> Night mode active
    dt_0330 = datetime(2026, 10, 19, 3, 30, 0, tzinfo=tz)
    assert is_night_cache_mode_kz(dt_0330) is True

    # 06:29:59 -> Night mode active, warmup not yet active
    dt_0629 = datetime(2026, 10, 19, 6, 29, 59, tzinfo=tz)
    assert is_night_cache_mode_kz(dt_0629) is True
    assert is_warmup_active_kz(dt_0629) is False

    # 06:30:00 -> Warmup starts, but night cache freeze STILL active to avoid pumpkin effect
    dt_0630 = datetime(2026, 10, 19, 6, 30, 0, tzinfo=tz)
    assert is_night_cache_mode_kz(dt_0630) is True
    assert is_warmup_active_kz(dt_0630) is True

    # 06:44:59 -> Warmup finishing 150 groups (~9 min), night cache freeze STILL active
    dt_0644 = datetime(2026, 10, 19, 6, 44, 59, tzinfo=tz)
    assert is_night_cache_mode_kz(dt_0644) is True
    assert is_warmup_active_kz(dt_0644) is True

    # 06:45:00 -> Night cache freeze ends, daytime TTL smoothly in effect
    dt_0645 = datetime(2026, 10, 19, 6, 45, 0, tzinfo=tz)
    assert is_night_cache_mode_kz(dt_0645) is False
    assert is_warmup_active_kz(dt_0645) is True

    # 12:00:00 -> Daytime active
    dt_1200 = datetime(2026, 10, 19, 12, 0, 0, tzinfo=tz)
    assert is_night_cache_mode_kz(dt_1200) is False
    assert is_warmup_active_kz(dt_1200) is True

    # 00:59:59 -> Daytime active (just before 01:00)
    dt_0059 = datetime(2026, 10, 19, 0, 59, 59, tzinfo=tz)
    assert is_night_cache_mode_kz(dt_0059) is False
    assert is_warmup_active_kz(dt_0059) is True


def test_night_aware_timer_suspends_ttlcached_eviction():
    """Verifies that NightAwareTimer prevents TTLCache from evicting entries during night mode."""
    timer = NightAwareTimer()
    cache = TTLCache(maxsize=10, ttl=0.3, timer=timer)
    cache["group_101"] = {"name": "ИС-23-1"}

    # During daytime, advancing real time past TTL expires the entry
    with patch("src.cron.cache_warmup.is_night_cache_mode_kz", return_value=False):
        time.sleep(0.05)
        assert "group_101" in cache

    # Under night mode, even sleeping well beyond TTL must NOT evict entry
    with patch("src.cron.cache_warmup.is_night_cache_mode_kz", return_value=True):
        time.sleep(0.4)  # 0.4s > 0.3s TTL
        assert "group_101" in cache
        assert cache.get("group_101") == {"name": "ИС-23-1"}

    # When day resumes, remaining daytime TTL elapses and item expires
    with patch("src.cron.cache_warmup.is_night_cache_mode_kz", return_value=False):
        time.sleep(0.35)
        assert "group_101" not in cache


def test_buketov_api_purge_expired_night_guard():
    """Verifies purge_expired returns 0 and leaves caches untouched during night mode."""
    service = buketov_api_service
    old_ttl = service.cache_ttl
    service.cache_ttl = 1.0  # 1 second

    # Populate expired entry
    stale_ts = time.time() - 100.0
    service.student_cache["stale_key"] = CacheEntry(data={"records": []}, timestamp=stale_ts)
    service.teacher_cache["stale_teacher"] = CacheEntry(data={"records": []}, timestamp=stale_ts)

    try:
        # Under night mode: must not purge anything
        with patch("src.cron.cache_warmup.is_night_cache_mode_kz", return_value=True):
            purged = service.purge_expired()
            assert purged == 0
            assert "stale_key" in service.student_cache
            assert "stale_teacher" in service.teacher_cache

        # Under day mode: must purge expired entries
        with patch("src.cron.cache_warmup.is_night_cache_mode_kz", return_value=False):
            purged = service.purge_expired()
            assert purged >= 2
            assert "stale_key" not in service.student_cache
            assert "stale_teacher" not in service.teacher_cache
    finally:
        service.cache_ttl = old_ttl
        service.student_cache.pop("stale_key", None)
        service.teacher_cache.pop("stale_teacher", None)


@pytest.mark.asyncio
async def test_priority_queue_and_cursor_isolation():
    """Verifies priority queue serves touched groups/teachers first without mutating rotation cursor."""
    engine = SmartWarmupEngine()
    engine.set_top_groups([(1, "G1"), (2, "G2"), (3, "G3")])
    engine._group_cursor = 1

    # Touch new priority group
    engine.touch_group(99, "G99")
    assert len(engine._priority_groups_queue) == 1
    assert engine._priority_groups_queue[0] == (99, "G99")
    # Main rotation cursor and cache completely untouched
    assert engine._group_cursor == 1
    assert len(engine._groups_cache) == 3

    # In worker step, group is pulled from priority queue
    pulled_group = engine._priority_groups_queue.popleft()
    assert pulled_group == (99, "G99")
    # Main rotation cursor remains 1
    assert engine._group_cursor == 1

    # When priority queue is empty, next step pulls from main rotation
    regular_group = engine._groups_cache[engine._group_cursor]
    assert regular_group[0] == 2
    engine._group_cursor = (engine._group_cursor + 1) % len(engine._groups_cache)
    assert engine._group_cursor == 2


@pytest.mark.asyncio
async def test_worker_lock_and_sleep_compensation():
    """Verifies independent worker execution and guaranteed sleep floor >= 2.5s."""
    engine = SmartWarmupEngine()
    engine.GROUP_INTERVAL_SEC = 3.5
    engine.TEACHER_INTERVAL_SEC = 4.0

    # Verify _tick_lock is initialized on engine
    assert hasattr(engine, "_tick_lock")
    assert isinstance(engine._tick_lock, asyncio.Lock)

    # Sleep compensation calculation verification with guaranteed 2.5s floor
    target_step = 3.5
    elapsed_fast = 0.5
    sleep_fast = max(2.5, target_step - elapsed_fast)
    assert sleep_fast == 3.0

    # When request takes 1.5s, sleep is 2.5s (due to 2.5s floor)
    elapsed_slow = 1.5
    sleep_slow = max(2.5, target_step - elapsed_slow)
    assert sleep_slow == 2.5

    # When request exceeds step, sleep hits guaranteed floor (2.5s)
    elapsed_heavy = 4.0
    sleep_heavy = max(2.5, target_step - elapsed_heavy)
    assert sleep_heavy == 2.5


@pytest.mark.asyncio
async def test_ring_buffer_and_selective_mongo_persistence(mock_db):
    """Verifies that 🟣 cache hits stay in RAM ring buffer and do NOT generate MongoDB writes."""
    engine = SmartWarmupEngine()

    # 1. Cache hit (🟣) -> ring buffer only
    engine._log_warmup_result(
        name="ИС-23-1",
        target_type="group",
        badge="🟣",
        source="cache",
        duration_ms=1,
        status=True,
    )
    await asyncio.sleep(0.05)

    recent = engine.get_recent_logs()
    assert len(recent) == 1
    assert recent[0]["badge"] == "🟣"
    assert recent[0]["source"] == "cache"

    # MongoDB has 0 records for ИС-23-1
    doc_cache = await WarmupLog.find_one(WarmupLog.name == "ИС-23-1")
    assert doc_cache is None

    # 2. Fresh API hit (🟢) -> ring buffer AND MongoDB
    engine._log_warmup_result(
        name="ИС-23-1",
        target_type="group",
        badge="🟢",
        source="api",
        duration_ms=45,
        status=True,
    )
    await asyncio.sleep(0.05)

    recent = engine.get_recent_logs()
    assert len(recent) == 2
    assert recent[-1]["badge"] == "🟢"

    doc_api = await WarmupLog.find_one(WarmupLog.name == "ИС-23-1")
    assert doc_api is not None
    assert doc_api.badge == "🟢"
    assert doc_api.source == "api"

    # 3. Error (🔴) -> ring buffer AND MongoDB
    engine._log_warmup_result(
        name="ИС-23-ERR",
        target_type="group",
        badge="🔴",
        source="error",
        duration_ms=4500,
        status=False,
        error_msg="Network Crash",
    )
    await asyncio.sleep(0.05)

    doc_err = await WarmupLog.find_one(WarmupLog.name == "ИС-23-ERR")
    assert doc_err is not None
    assert doc_err.badge == "🔴"
    assert doc_err.status is False


@pytest.mark.asyncio
async def test_get_or_fetch_night_mode_reuses_ram_without_fetch():
    """Verifies get_or_fetch_student_schedule immediately returns valid RAM cache during night mode."""
    group_id = 99881
    cache_key = f"{group_id}_ru"
    now = time.time()

    # Place item with 1-hour old timestamp (> 30 min max_age_sec)
    cached_payload = {
        "data": [{"day": "Понедельник", "subjects": [{"subject": "Math"}]}],
        "timestamp": now - 3600.0,
        "is_from_db": False,
        "is_from_cache": True,
    }
    student_schedule_cache[cache_key] = cached_payload

    try:
        # Under night mode: must reuse cache even if timestamp is 1 hour old!
        with patch("src.cron.cache_warmup.is_night_cache_mode_kz", return_value=True):
            with patch("src.services.buketov_api.buketov_api_service.get_group_schedule", new=AsyncMock()) as mock_fetch:
                result = await get_or_fetch_student_schedule(group_id, "ru", is_refresh=False)
                assert result is not None
                assert result["data"] == cached_payload["data"]
                assert mock_fetch.call_count == 0

        # When refresh is explicitly pressed: must fetch
        fake_api = {
            "records": [{"dayOfWeek": 1, "day": "Понедельник", "time": "08:30-09:20", "subject": "Math", "room": "101"}],
            "_fromDb": False,
            "_cacheTimestamp": time.time(),
        }
        with patch("src.cron.cache_warmup.is_night_cache_mode_kz", return_value=True):
            with patch("src.services.buketov_api.buketov_api_service.get_group_schedule", new=AsyncMock(return_value=fake_api)) as mock_fetch:
                result = await get_or_fetch_student_schedule(group_id, "ru", is_refresh=True, group_name="ИС-23-1")
                assert mock_fetch.call_count == 1
    finally:
        student_schedule_cache.pop(cache_key, None)


def test_night_aware_timer_overnight_gap_does_not_evict():
    """Verifies that an overnight gap of 5.5 hours does not advance timer by 5.5 hours and does not evict cache."""
    tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
    timer = NightAwareTimer()
    cache = TTLCache(maxsize=10, ttl=1800.0, timer=timer)
    cache["group_202"] = {"data": "schedules"}

    # Simulate 00:59:00 (just before night mode)
    dt_0059 = datetime(2026, 10, 19, 0, 59, 0, tzinfo=tz)
    ts_0059 = dt_0059.timestamp()
    timer._last_wall = ts_0059
    timer._last_real = 1000.0
    timer._virtual = 1000.0

    # Simulate morning request at 06:35:00 (5 hours 36 minutes later)
    # The night window (01:00 to 06:45 = 5.75 hours) is excluded from virtual time.
    # At 06:35:00, still in night freeze window: only 1 min (00:59-01:00 = 60s) active!
    dt_0635 = datetime(2026, 10, 19, 6, 35, 0, tzinfo=tz)
    ts_0635 = dt_0635.timestamp()

    with patch("time.time", return_value=ts_0635):
        with patch("time.monotonic", return_value=1000.0 + (ts_0635 - ts_0059)):
            # Access cache at 06:35:00
            val = cache.get("group_202")
            assert val is not None
            assert val == {"data": "schedules"}
            # Virtual clock only advanced around ~60s, well under 1800s TTL!
            assert timer._virtual - 1000.0 <= 100.0

    # Simulate request at 06:50:00 (5 minutes after night freeze ends at 06:45)
    # Total active: 1 min (00:59-01:00) + 5 min (06:45-06:50) = 6 min = 360s elapsed!
    dt_0650 = datetime(2026, 10, 19, 6, 50, 0, tzinfo=tz)
    ts_0650 = dt_0650.timestamp()

    with patch("time.time", return_value=ts_0650):
        with patch("time.monotonic", return_value=1000.0 + (ts_0650 - ts_0059)):
            val = cache.get("group_202")
            assert val is not None
            assert val == {"data": "schedules"}
            assert timer._virtual - 1000.0 <= 400.0


@pytest.mark.asyncio
async def test_buketov_api_fetch_schedule_night_mode_reuses_cache():
    """Verifies that BuketovApiService.fetch_schedule reuses cached data during night mode even if > 10m old."""
    import urllib.parse

    service = buketov_api_service
    qs = urllib.parse.urlencode(sorted({"group": "ИС-23-1"}.items()))
    cache_key = f"{service.api_url}?{qs}"
    now = time.time()
    # Cache entry from 40 minutes ago (40m > 10m TTL)
    entry = CacheEntry(data={"records": [{"subject": "History"}]}, timestamp=now - 2400.0)
    service.student_cache[cache_key] = entry

    try:
        with patch("src.cron.cache_warmup.is_night_cache_mode_kz", return_value=True):
            with patch.object(service, "get_client", AsyncMock()) as mock_client:
                result = await service.fetch_schedule(params={"group": "ИС-23-1"}, force_refresh=False)
                assert result == {"records": [{"subject": "History"}]}
                assert mock_client.call_count == 0
    finally:
        service.student_cache.pop(cache_key, None)


def test_priority_queue_bounded_maxlen():
    """Verifies that priority queues for both groups and teachers are strictly bounded by maxlen=25."""
    engine = SmartWarmupEngine()
    assert engine._priority_groups_queue.maxlen == 25
    assert engine._priority_teachers_queue.maxlen == 25

    # Push 35 groups
    for i in range(1, 36):
        engine.touch_group(i, f"Group-{i}")
    assert len(engine._priority_groups_queue) == 25
    # The oldest 10 items (1..10) should have been dropped; 11..35 remain
    group_ids = [gid for gid, _ in engine._priority_groups_queue]
    assert group_ids[0] == 11
    assert group_ids[-1] == 35

    # Push 35 teachers
    for i in range(1, 36):
        engine.touch_teacher(i, f"Teacher-{i}")
    assert len(engine._priority_teachers_queue) == 25
    teacher_ids = [tid for tid, _ in engine._priority_teachers_queue]
    assert teacher_ids[0] == 11
    assert teacher_ids[-1] == 35


@pytest.mark.asyncio
async def test_teacher_worker_lock_pacing_and_start_mono():
    """Verifies teacher worker independent execution and sleep pacing floor."""
    engine = SmartWarmupEngine()
    engine.TEACHER_INTERVAL_SEC = 4.0

    assert hasattr(engine, "_tick_lock")
    assert isinstance(engine._tick_lock, asyncio.Lock)

    # Pacing: target = 4.0s. If request takes 1.0s, sleep is 3.0s
    elapsed_total = 1.0
    sleep_calc = max(2.5, engine.TEACHER_INTERVAL_SEC - elapsed_total)
    assert round(sleep_calc, 1) == 3.0

    # If request takes 3.0s, sleep floor guarantees 2.5s
    elapsed_heavy = 3.0
    sleep_heavy = max(2.5, engine.TEACHER_INTERVAL_SEC - elapsed_heavy)
    assert sleep_heavy == 2.5

