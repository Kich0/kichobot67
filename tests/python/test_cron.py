"""Unit and integration tests for Phase 6 Cron Jobs and APScheduler."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import zoneinfo
from src.core.constants import TIMEZONE_NAME
from src.bot.handlers.callbacks_router import (
    clear_schedule_caches,
    student_schedule_cache,
    teacher_table_cache,
    teacher_text_cache,
)
from src.cron import (
    SmartWarmupEngine,
    check_and_enforce_memory_limit,
    get_kz_start_of_day,
    get_scheduler,
    is_warmup_active_kz,
    log_daily_user_statistics,
    run_daily_data_update,
    setup_all_crons,
    setup_cache_warmup,
    setup_daily_stats,
    setup_daily_update,
    setup_memory_trim,
    smart_warmup_engine,
    start_scheduler,
    stop_scheduler,
    trim_process_memory,
    warmup_top_schedules,
)
from src.database.models.group import Group
from src.database.models.user import User
from src.database.models.user_action import UserAction
from src.database.models.user_activity import UserActivity
from src.database.models.user_registration_stat import UserRegistrationStat
from src.services.buketov_api import buketov_api_service


@pytest.mark.asyncio
async def test_scheduler_lifecycle():
    """Tests scheduler singleton, starting, and stopping."""
    sched = get_scheduler()
    assert isinstance(sched, AsyncIOScheduler)
    assert sched.timezone == zoneinfo.ZoneInfo(TIMEZONE_NAME)

    start_scheduler()
    assert sched.running is True

    stop_scheduler()
    await asyncio.sleep(0.05)
    assert sched.running is False


@pytest.mark.asyncio
async def test_cron_registration():
    """Verifies that setup_all_crons registers the three required jobs with correct timings."""
    sched = AsyncIOScheduler(timezone=zoneinfo.ZoneInfo(TIMEZONE_NAME))
    setup_all_crons(sched)

    job_ids = [job.id for job in sched.get_jobs()]
    assert "daily_data_update" in job_ids
    assert "cache_warmup" in job_ids
    assert "daily_user_statistics" in job_ids

    # Verify triggers
    daily_job = sched.get_job("daily_data_update")
    warmup_job = sched.get_job("cache_warmup")
    stats_job = sched.get_job("daily_user_statistics")

    assert str(daily_job.trigger) == "cron[hour='6', minute='0']"
    assert str(warmup_job.trigger) == "cron[hour='7', minute='30']"
    assert str(stats_job.trigger) == "cron[hour='23', minute='59']"


@pytest.mark.asyncio
async def test_run_daily_data_update_no_auto_auth():
    """Tests daily update when AUTO_KSU_AUTH is False."""
    with patch("src.cron.daily_update.settings.AUTO_KSU_AUTH", False):
        # Should complete cleanly without error
        await run_daily_data_update(is_startup=False)
        await run_daily_data_update(is_startup=True)


@pytest.mark.asyncio
async def test_warmup_top_schedules(mock_db):
    """Tests morning schedule cache warmup on top active student groups."""
    # Create groups
    g1 = Group(id=101, name="CS-21", faculty="FIT", course=2)
    g2 = Group(id=102, name="CS-22", faculty="FIT", course=2)
    await g1.insert()
    await g2.insert()

    # Create users belonging to groups
    await User(userId=1001, group=101).insert()
    await User(userId=1002, group=101).insert()
    await User(userId=1003, group=102).insert()

    mock_fetch = AsyncMock(return_value={"records": [{"subject": "Math"}], "total": 1})
    with patch("src.cron.cache_warmup.buketov_api_service.fetch_schedule", mock_fetch):
        count = await warmup_top_schedules(limit=5)
        assert count == 2
        assert mock_fetch.await_count == 2


@pytest.mark.asyncio
async def test_log_daily_user_statistics(mock_db):
    """Tests 23:59 daily statistics calculation and persistence."""
    start_utc = get_kz_start_of_day()

    # Create user actions today
    await UserAction(userId=201, action="test", text="action 1", createdAt=start_utc).insert()
    await UserAction(userId=201, action="test", text="action 2", createdAt=start_utc).insert()
    await UserAction(userId=202, action="test", text="action 3", createdAt=start_utc).insert()

    # Create registered users today
    await User(userId=201, createdAt=start_utc).insert()
    await User(userId=202, createdAt=start_utc).insert()

    res = await log_daily_user_statistics()
    assert res["active_users"] == 2
    assert res["registered_users"] == 2

    # Check database records
    activities = await UserActivity.find().to_list()
    assert len(activities) == 1
    assert activities[0].user_activity == 2

    registrations = await UserRegistrationStat.find().to_list()
    assert len(registrations) == 1
    assert registrations[0].registered_users == 2


def test_get_kz_start_of_day():
    """Tests timezone-aware start of day computation."""
    start_dt = get_kz_start_of_day()
    assert start_dt.tzinfo == timezone.utc
    # In KZ (UTC+5), 00:00:00 KZ is 19:00:00 UTC of previous day
    kz_tz = zoneinfo.ZoneInfo("Asia/Almaty")
    in_kz = start_dt.astimezone(kz_tz)
    assert in_kz.hour == 0
    assert in_kz.minute == 0
    assert in_kz.second == 0
    assert in_kz.microsecond == 0


@pytest.mark.asyncio
async def test_warmup_top_schedules_fallback_to_groups(mock_db):
    """Tests that when users have fewer groups than limit, warmup queries extra groups from Group collection."""
    # 3 groups in total in database
    g1 = Group(id=301, name="IS-1")
    g2 = Group(id=302, name="IS-2")
    g3 = Group(id=303, name="IS-3")
    await g1.insert()
    await g2.insert()
    await g3.insert()

    # Only 1 group has an active user
    await User(userId=9001, group=301).insert()

    mock_fetch = AsyncMock(return_value={"records": [{"subject": "Networks"}], "total": 1})
    with patch("src.cron.cache_warmup.buketov_api_service.fetch_schedule", mock_fetch):
        # limit is 3, so 1 comes from User aggregate, 2 come from Group NotIn fallback
        warmed = await warmup_top_schedules(limit=3)
        assert warmed == 3
        assert mock_fetch.await_count == 3


@pytest.mark.asyncio
async def test_daily_stats_query_structure(mock_db):
    """Verifies that Beanie filter query preserves $or and $gte operators."""
    from beanie.operators import GTE, Or

    start_utc = get_kz_start_of_day()
    start_naive = start_utc.replace(tzinfo=None)

    q = User.find(
        Or(
            GTE(User.created_at, start_utc),
            GTE(User.created_at, start_naive),
        )
    )
    filter_dict = q.get_filter_query()
    assert "$or" in filter_dict
    assert len(filter_dict["$or"]) == 2
    assert "$gte" in filter_dict["$or"][0]["createdAt"]


def test_is_warmup_active_kz_boundaries():
    """Tests exact boundary hours for Kazakhstan schedule warmup window (06:30 - 01:00)."""
    kz_tz = zoneinfo.ZoneInfo("Asia/Almaty")

    # Morning edge: 06:29:59 is sleeping
    dt_629 = datetime(2026, 10, 16, 6, 29, 59, tzinfo=kz_tz)
    assert is_warmup_active_kz(dt_629) is False

    # Morning edge: 06:30:00 is active
    dt_630 = datetime(2026, 10, 16, 6, 30, 0, tzinfo=kz_tz)
    assert is_warmup_active_kz(dt_630) is True

    # Midday: 12:00:00 is active
    dt_1200 = datetime(2026, 10, 16, 12, 0, 0, tzinfo=kz_tz)
    assert is_warmup_active_kz(dt_1200) is True

    # Late evening: 23:59:59 is active
    dt_2359 = datetime(2026, 10, 16, 23, 59, 59, tzinfo=kz_tz)
    assert is_warmup_active_kz(dt_2359) is True

    # Midnight: 00:00:00 is active
    dt_0000 = datetime(2026, 10, 17, 0, 0, 0, tzinfo=kz_tz)
    assert is_warmup_active_kz(dt_0000) is True

    # Night edge: 00:59:59 is active
    dt_0059 = datetime(2026, 10, 17, 0, 59, 59, tzinfo=kz_tz)
    assert is_warmup_active_kz(dt_0059) is True

    # Night edge: 01:00:00 is sleeping
    dt_0100 = datetime(2026, 10, 17, 1, 0, 0, tzinfo=kz_tz)
    assert is_warmup_active_kz(dt_0100) is False

    # Night: 03:30:00 is sleeping
    dt_0330 = datetime(2026, 10, 17, 3, 30, 0, tzinfo=kz_tz)
    assert is_warmup_active_kz(dt_0330) is False

    # Naive datetime handling
    naive_active = datetime(2026, 10, 16, 14, 0, 0)
    assert is_warmup_active_kz(naive_active) is True

    naive_sleep = datetime(2026, 10, 16, 2, 0, 0)
    assert is_warmup_active_kz(naive_sleep) is False

    # UTC conversion: 01:30 UTC = 06:30 KZ (+5)
    utc_active = datetime(2026, 10, 16, 1, 30, 0, tzinfo=timezone.utc)
    assert is_warmup_active_kz(utc_active) is True

    # Default call without arguments
    res = is_warmup_active_kz()
    assert isinstance(res, bool)


def test_smart_warmup_engine_pacing_and_intervals():
    """Validates SmartWarmupEngine pacing math and API rate limit headroom."""
    engine = SmartWarmupEngine()
    assert engine.GROUP_INTERVAL_SEC == 3.5
    assert engine.TEACHER_INTERVAL_SEC == 4.0
    assert engine.TOP_GROUPS_LIMIT == 150
    assert engine.TOP_TEACHERS_LIMIT == 60
    assert engine.HEADROOM == 5

    group_req_per_min = 60.0 / engine.GROUP_INTERVAL_SEC  # ~17.14 req/min
    teacher_req_per_min = 60.0 / engine.TEACHER_INTERVAL_SEC  # 15.0 req/min
    total_req_per_min = group_req_per_min + teacher_req_per_min  # ~32.14 req/min

    assert round(total_req_per_min, 1) == 32.1
    # Must consume under 30% of university 110 req/min limit, leaving > 70% free
    assert total_req_per_min <= 110.0 * 0.30


@pytest.mark.asyncio
async def test_smart_warmup_engine_lifecycle():
    """Tests SmartWarmupEngine start, pause, resume, and graceful stop."""
    engine = SmartWarmupEngine()
    engine.SLEEP_CHECK_INTERVAL_SEC = 0.05
    engine.PAUSE_CHECK_INTERVAL_SEC = 0.05

    assert engine.is_running is False
    assert engine.is_paused() is False

    engine.start()
    assert engine.is_running is True
    assert engine._group_task is not None
    assert engine._teacher_task is not None

    # Pause
    engine.pause(duration_seconds=10.0)
    assert engine.is_paused() is True

    # Resume
    engine.resume()
    assert engine.is_paused() is False

    # Stop gracefully
    await engine.stop_async()
    assert engine.is_running is False
    assert engine._group_task is None
    assert engine._teacher_task is None


@pytest.mark.asyncio
async def test_smart_warmup_engine_rate_limit_headroom_skip():
    """Verifies that engine skips fetch when API rate limit headroom is reached."""
    engine = SmartWarmupEngine()
    mock_warm = AsyncMock()
    engine._warm_group = mock_warm

    with patch("src.cron.cache_warmup.buketov_api_service.can_make_api_call", return_value=False):
        with patch("src.cron.cache_warmup.is_warmup_active_kz", return_value=True):
            engine.set_top_groups([(999, "TEST-99")])
            engine.GROUP_INTERVAL_SEC = 0.01

            engine.start()
            await asyncio.sleep(0.05)
            await engine.stop_async()

            # Should have skipped network fetch
            assert mock_warm.await_count == 0


@pytest.mark.asyncio
async def test_smart_warmup_engine_touch_group_and_carousel():
    """Verifies that touch_group dynamically enrolls group into priority queue and carousel."""
    engine = SmartWarmupEngine()
    engine.set_top_groups([(1, "G-1"), (2, "G-2"), (3, "G-3")])
    assert len(engine._groups_cache) == 3

    # Touch a new group: enqueued into priority queue without mutating rotation cache
    engine.touch_group(105, "CS-25")
    assert 105 in engine._priority_groups
    assert engine._priority_groups[105][0] == "CS-25"
    assert (105, "CS-25") in engine._priority_groups_queue
    assert len(engine._groups_cache) == 3

    # Touch teacher: enqueued into priority queue without mutating rotation cache
    engine.set_top_teachers([(10, "T-1"), (20, "T-2")])
    engine.touch_teacher(99, "Сидоров С. С.")
    assert 99 in engine._priority_teachers
    assert (99, "Сидоров С. С.") in engine._priority_teachers_queue
    assert len(engine._teachers_cache) == 2


@pytest.mark.asyncio
async def test_smart_warmup_engine_warm_group_and_teacher(mock_db):
    """Tests _warm_group and _warm_teacher fetch, adaptation, caching, and persistence."""
    engine = SmartWarmupEngine()

    sample_group_data = {
        "records": [
            {
                "day": "Понедельник",
                "time": "08:30-09:50",
                "subject": "Физика",
                "teacher": "Иванов И. И.",
                "auditory": "101",
                "weekType": "числитель",
            }
        ],
        "total": 1,
    }

    sample_teacher_data = {
        "records": [
            {
                "day": "Вторник",
                "time": "10:00-11:20",
                "subject": "Математика",
                "group": "CS-21",
                "auditory": "202",
                "weekType": "знаменатель",
            }
        ],
        "total": 1,
    }

    with patch("src.cron.cache_warmup.buketov_api_service.get_group_schedule", AsyncMock(return_value=sample_group_data)) as mock_g:
        await engine._warm_group(group_id=555, group_name="CS-55")
        mock_g.assert_called_once_with("CS-55", force_refresh=True)
        assert "555_ru" in student_schedule_cache
        assert "555_kz" in student_schedule_cache
        assert student_schedule_cache["555_ru"]["data"] is not None

    with patch("src.cron.cache_warmup.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=sample_teacher_data)) as mock_t:
        await engine._warm_teacher(teacher_id=777, teacher_name="Петров П. П.")
        mock_t.assert_called_once_with("Петров П. П.", force_refresh=True)
        assert "777_ru" in teacher_text_cache
        assert "777_kz" in teacher_text_cache
        assert teacher_text_cache["777_ru"]["data"] is not None


def test_emergency_memory_guard_below_threshold():
    """Verifies that OOM guard does nothing when process memory is in safe green zone."""
    student_schedule_cache["safe_key"] = {"data": "ok"}
    with patch("src.api.health.get_process_memory_mb", return_value=240.0):
        triggered = check_and_enforce_memory_limit(threshold_mb=430.0)
        assert triggered is False
        assert "safe_key" in student_schedule_cache


def test_emergency_memory_guard_above_threshold():
    """Verifies that OOM guard sheds caches, triggers GC, and pauses engine at >= 430 MB."""
    # Pre-populate caches
    student_schedule_cache["group_1"] = {"data": "test"}
    teacher_text_cache["teacher_1"] = {"data": "test"}
    teacher_table_cache["teacher_tbl_1"] = {"data": "test"}
    buketov_api_service.student_cache["api_key_1"] = MagicMock()
    buketov_api_service.teacher_cache["api_key_2"] = MagicMock()

    smart_warmup_engine.resume()
    assert smart_warmup_engine.is_paused() is False

    with patch("src.api.health.get_process_memory_mb", return_value=445.0):
        with patch("src.cron.memory_trim.trim_process_memory") as mock_trim:
            triggered = check_and_enforce_memory_limit(threshold_mb=430.0)

            assert triggered is True
            # Caches cleared
            assert len(student_schedule_cache) == 0
            assert len(teacher_text_cache) == 0
            assert len(teacher_table_cache) == 0
            assert len(buketov_api_service.student_cache) == 0
            assert len(buketov_api_service.teacher_cache) == 0
            # Warmup engine paused
            assert smart_warmup_engine.is_paused() is True
            # Memory trim invoked
            assert mock_trim.called


def test_emergency_memory_guard_scheduler_job():
    """Verifies that setup_memory_trim configures the 60s emergency guard job."""
    sched = AsyncIOScheduler(timezone=zoneinfo.ZoneInfo(TIMEZONE_NAME))
    setup_memory_trim(sched)

    job_ids = [job.id for job in sched.get_jobs()]
    assert "memory_trim_job" in job_ids
    assert "memory_guard_job" in job_ids

    guard_job = sched.get_job("memory_guard_job")
    assert str(guard_job.trigger) == "interval[0:01:00]"


@pytest.mark.asyncio
async def test_smart_warmup_engine_cursor_round_robin_no_starvation():
    """Verifies that engine advances through groups round-robin without restarting at 0 after pauses."""
    engine = SmartWarmupEngine()
    warmed_sequence: list[str] = []

    async def fake_warm_group(group_id: int, group_name: str) -> None:
        warmed_sequence.append(group_name)

    engine._warm_group = fake_warm_group  # type: ignore[assignment]
    engine._warm_teacher = AsyncMock()  # type: ignore[assignment]
    engine.set_top_groups([(1, "G-1"), (2, "G-2"), (3, "G-3")])
    engine.GROUP_INTERVAL_SEC = 0.01
    engine.PAUSE_CHECK_INTERVAL_SEC = 0.01

    with patch("src.cron.cache_warmup.is_warmup_active_kz", return_value=True):
        engine.start()
        # Let it warm G-1
        await asyncio.sleep(0.015)
        # Pause briefly
        engine.pause(0.05)
        assert engine.is_paused() is True
        await asyncio.sleep(0.08)
        assert engine.is_paused() is False
        # Let it warm subsequent groups
        await asyncio.sleep(0.04)
        await engine.stop_async()

    # The sequence should progress G-1, G-2, G-3 without resetting back to G-1 when unpaused
    assert len(warmed_sequence) >= 2
    assert warmed_sequence[0] == "G-1"
    assert warmed_sequence[1] == "G-2"


@pytest.mark.asyncio
async def test_smart_warmup_engine_teacher_directory_fallback(mock_db):
    """Verifies that _get_top_teachers supplements from teacher_directory_service without AttributeError."""
    engine = SmartWarmupEngine()
    engine._teachers_cache = []
    engine._teachers_cache_updated_at = 0.0

    # With empty DB, should load from teacher_directory_service.teachers
    teachers = await engine._get_top_teachers()
    assert isinstance(teachers, list)
    assert len(teachers) > 0
    assert len(teachers) <= engine.TOP_TEACHERS_LIMIT


def test_get_process_memory_mb_status_file():
    """Verifies that get_process_memory_mb parses VmRSS from /proc/self/status when available."""
    from unittest.mock import mock_open
    from src.api.health import get_process_memory_mb

    fake_status = "Name:\tpython\nVmPeak:\t 500000 kB\nVmRSS:\t  153600 kB\nVmData:\t 100000 kB\n"
    with patch("builtins.open", mock_open(read_data=fake_status)):
        mem_mb = get_process_memory_mb()
        assert mem_mb == 150.0  # 153600 / 1024


@pytest.mark.asyncio
async def test_smart_warmup_empty_db_preserves_valid_cache():
    """Verifies that empty DB fallback doesn't overwrite existing non-empty cache."""
    engine = SmartWarmupEngine()

    student_schedule_cache["888_ru"] = {"data": [{"day": "Понедельник", "subjects": ["Math"]}], "timestamp": 123.0}
    empty_db_response = {"records": [], "total": 0, "_fromDb": True}

    with patch("src.cron.cache_warmup.buketov_api_service.get_group_schedule", AsyncMock(return_value=empty_db_response)):
        await engine._warm_group(group_id=888, group_name="EXISTING-88")
        # Should keep existing valid data, not overwrite with empty
        assert student_schedule_cache["888_ru"]["data"] == [{"day": "Понедельник", "subjects": ["Math"]}]


@pytest.mark.asyncio
async def test_smart_warmup_engine_background_task_retention():
    """Verifies that _create_background_task tracks and cleans background tasks."""
    engine = SmartWarmupEngine()

    async def dummy_coro():
        await asyncio.sleep(0.02)

    task = engine._create_background_task(dummy_coro())
    assert task in engine._background_tasks
    await task
    await asyncio.sleep(0.01)
    assert task not in engine._background_tasks


@pytest.mark.asyncio
async def test_teacher_warmup_and_text_fetch_cache_isolation():
    """Verifies that _warm_teacher and fetch_teacher_text_schedule touch ONLY text cache, NOT table cache."""
    clear_schedule_caches()
    engine = SmartWarmupEngine()

    dummy_schedule = {
        "records": [{"day": "Понедельник", "time": "09:00", "subject": "Math", "lessonType": "лек", "group": "ИС-21-1"}],
        "_cacheTimestamp": 1000.0,
        "_fromDb": False,
    }

    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", AsyncMock(return_value=(555, "Петров П. П.", 10, "Петров П. П."))), \
         patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=dummy_schedule)), \
         patch("src.database.models.teacher.Teacher.find_one", AsyncMock(return_value=None)):

        # 1. Warm teacher via SmartWarmupEngine
        await engine._warm_teacher(555, "Петров П. П.")

        # Text cache must be populated
        assert "555_ru" in teacher_text_cache
        assert "555_kz" in teacher_text_cache
        # Table cache must remain strictly empty
        assert len(teacher_table_cache) == 0
        assert "555" not in teacher_table_cache

        # 2. Fetch text schedule via callbacks_router
        from src.bot.handlers.callbacks_router import get_or_fetch_teacher_text_schedule
        text_entry = await get_or_fetch_teacher_text_schedule(555, "ru", is_refresh=False, teacher_name="Петров П. П.")
        assert text_entry is not None
        assert text_entry["timestamp"] == 1000.0
        # Table cache must STILL remain strictly empty
        assert len(teacher_table_cache) == 0

        # 3. Independent freshness: table cache has its own independent entry and timestamp
        teacher_table_cache["555"] = {"data": [], "timestamp": 2000.0, "is_from_db": False}
        assert teacher_text_cache["555_ru"]["timestamp"] == 1000.0
        assert teacher_table_cache["555"]["timestamp"] == 2000.0

    clear_schedule_caches()


def test_trim_process_memory_rss_threshold():
    """Verifies that trim_process_memory only trims OS heap when memory >= 180 MB or force_os_trim=True."""
    from src.cron.memory_trim import HEALTHY_RSS_CEILING_MB, trim_process_memory

    assert HEALTHY_RSS_CEILING_MB == 180.0

    mock_libc = MagicMock()
    with patch("sys.platform", "linux"), \
         patch("ctypes.CDLL", return_value=mock_libc), \
         patch("src.api.health.get_process_memory_mb", return_value=150.0), \
         patch("src.services.buketov_api.buketov_api_service.purge_expired", return_value=0):

        # 1. Under threshold and force_os_trim=False: malloc_trim should NOT be called
        trim_process_memory(force_os_trim=False)
        mock_libc.malloc_trim.assert_not_called()

        # 2. Under threshold but force_os_trim=True: malloc_trim SHOULD be called
        trim_process_memory(force_os_trim=True)
        mock_libc.malloc_trim.assert_called_once_with(0)

    # 3. Over threshold (e.g. 210 MB): malloc_trim SHOULD be called even with force_os_trim=False
    mock_libc_high = MagicMock()
    with patch("sys.platform", "linux"), \
         patch("ctypes.CDLL", return_value=mock_libc_high), \
         patch("src.api.health.get_process_memory_mb", return_value=210.0), \
         patch("src.services.buketov_api.buketov_api_service.purge_expired", return_value=0):

        trim_process_memory(force_os_trim=False)
        mock_libc_high.malloc_trim.assert_called_once_with(0)


def test_touch_group_preserves_cursor_and_cache():
    """Verifies that touch_group does not shift cursor and does not mutate main cache."""
    engine = SmartWarmupEngine()
    engine.set_top_groups([(10, "G-10"), (20, "G-20"), (30, "G-30")])
    engine._group_cursor = 2

    # Touch existing group 20: cursor should remain 2, cache order preserved
    engine.touch_group(20, "G-20")
    assert engine._group_cursor == 2
    assert engine._groups_cache[0][0] == 10
    assert engine._groups_cache[1][0] == 20
    assert engine._groups_cache[2][0] == 30
    assert any(item[0] == 20 for item in engine._priority_groups_queue)

    # Touch new group 99: enqueued in priority queue, cursor and main cache unchanged
    engine.touch_group(99, "G-99")
    assert engine._group_cursor == 2  # Main cursor NOT shifted
    assert len(engine._groups_cache) == 3  # Main cache NOT mutated
    assert engine._groups_cache[0][0] == 10
    assert any(item[0] == 99 for item in engine._priority_groups_queue)
    assert engine._priority_groups_queue.maxlen == 25

    # Enqueue > 25 priority groups to verify bounded queue capacity
    for i in range(100, 135):
        engine.touch_group(i, f"G-{i}")
    assert len(engine._priority_groups_queue) == 25
    assert engine._priority_groups_queue.maxlen == 25


def test_touch_teacher_preserves_cursor():
    """Verifies that touch_teacher does not shift cursor and does not mutate main cache."""
    engine = SmartWarmupEngine()
    engine.set_top_teachers([(101, "T-1"), (102, "T-2"), (103, "T-3")])
    engine._teacher_cursor = 1

    # Touch existing teacher 103: cursor should remain 1
    engine.touch_teacher(103, "T-3")
    assert engine._teacher_cursor == 1
    assert any(item[0] == 103 for item in engine._priority_teachers_queue)

    # Touch new teacher 999: enqueued in priority queue, cursor and main cache unchanged
    engine.touch_teacher(999, "T-999")
    assert engine._teacher_cursor == 1  # Main cursor NOT shifted
    assert len(engine._teachers_cache) == 3  # Main cache NOT mutated
    assert any(item[0] == 999 for item in engine._priority_teachers_queue)
    assert engine._priority_teachers_queue.maxlen == 25

    # Enqueue > 25 priority teachers to verify bounded capacity
    for i in range(200, 235):
        engine.touch_teacher(i, f"T-{i}")
    assert len(engine._priority_teachers_queue) == 25
    assert engine._priority_teachers_queue.maxlen == 25


@pytest.mark.asyncio
async def test_warm_group_skips_recent_cache():
    """Verifies that _warm_group skips API network calls if group was cached < 180s ago."""
    engine = SmartWarmupEngine()
    clear_schedule_caches()

    # Pre-populate cache with fresh timestamp (10s ago)
    now = time.time()
    student_schedule_cache["555_ru"] = {
        "data": [{"day": "Понедельник", "time": "09:00", "subject": "Math"}],
        "timestamp": now - 10.0,
        "is_from_db": False,
    }

    with patch("src.cron.cache_warmup.buketov_api_service.get_group_schedule") as mock_get:
        await engine._warm_group(555, "CS-55")
        mock_get.assert_not_called()

    clear_schedule_caches()


@pytest.mark.asyncio
async def test_warm_teacher_skips_recent_cache():
    """Verifies that _warm_teacher skips API network calls if teacher was cached < 120s ago."""
    engine = SmartWarmupEngine()
    clear_schedule_caches()

    # Pre-populate cache with fresh timestamp (10s ago)
    now = time.time()
    teacher_text_cache["777_ru"] = {
        "data": [{"day": "Вторник", "time": "10:00", "subject": "Physics"}],
        "timestamp": now - 10.0,
        "is_from_db": False,
    }

    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", AsyncMock(return_value=(777, "Иванов И. И.", None, "Иванов И. И."))), \
         patch("src.cron.cache_warmup.buketov_api_service.get_teacher_schedule") as mock_get:
        await engine._warm_teacher(777, "Иванов И. И.")
        mock_get.assert_not_called()

    clear_schedule_caches()


@pytest.mark.asyncio
async def test_get_top_groups_14_day_filter():
    """Verifies that _get_top_groups adds a 14-day createdAt filter to UserAction aggregate."""
    engine = SmartWarmupEngine()
    captured_pipelines = []

    mock_agg = AsyncMock()
    mock_agg.to_list = AsyncMock(return_value=[])

    def capture_aggregate(pipeline):
        captured_pipelines.append(pipeline)
        return mock_agg

    with patch("src.database.models.user.User.aggregate") as mock_user_agg, \
         patch("src.database.models.user_action.UserAction.aggregate", side_effect=capture_aggregate), \
         patch("src.database.models.group.Group.find") as mock_find_grp:

        mock_user_agg.return_value.to_list = AsyncMock(return_value=[])
        mock_find_grp.return_value.to_list = AsyncMock(return_value=[])
        mock_find_grp.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

        await engine._get_top_groups()

        assert len(captured_pipelines) >= 1
        pipe = captured_pipelines[0]
        match_stage = pipe[0]["$match"]
        assert "createdAt" in match_stage
        assert "$gte" in match_stage["createdAt"]
        delta = datetime.now(timezone.utc) - match_stage["createdAt"]["$gte"]
        assert 13.9 <= delta.total_seconds() / (24 * 3600) <= 14.1


def test_trim_process_memory_meaningful_growth():
    """Verifies that meaningful memory growth (>= 35MB growth at >= 240MB) triggers malloc_trim."""
    import src.cron.memory_trim as mt
    from src.cron.memory_trim import trim_process_memory

    # Set initial baseline memory
    mt._last_rss_mb = 205.0

    mock_libc = MagicMock()
    # Grow from 205 to 250 MB (+45 MB growth at >= 240 MB)
    with patch("sys.platform", "linux"), \
         patch("ctypes.CDLL", return_value=mock_libc), \
         patch("src.api.health.get_process_memory_mb", return_value=250.0), \
         patch("src.services.buketov_api.buketov_api_service.purge_expired", return_value=0):

        trim_process_memory(force_os_trim=False, min_rss_mb=280.0)
        mock_libc.malloc_trim.assert_called_once_with(0)

    # Reset
    mt._last_rss_mb = 0.0


@pytest.mark.asyncio
async def test_warm_group_null_timestamp_does_not_crash():
    """Verifies that _warm_group does not throw TypeError when existing cache entry has timestamp=None."""
    engine = SmartWarmupEngine()
    clear_schedule_caches()

    student_schedule_cache["999_ru"] = {
        "data": [{"day": "Понедельник", "time": "09:00", "subject": "Math"}],
        "timestamp": None,
        "is_from_db": False,
    }

    dummy_raw = {
        "records": [{"day": "Понедельник", "time": "09:00", "subject": "Math"}],
        "_cacheTimestamp": time.time(),
        "_fromDb": False,
    }

    with patch("src.cron.cache_warmup.buketov_api_service.get_group_schedule", AsyncMock(return_value=dummy_raw)):
        # Must not raise TypeError: float() argument must be a string or a real number, not 'NoneType'
        await engine._warm_group(999, "CS-99")
        assert "999_ru" in student_schedule_cache

    clear_schedule_caches()


@pytest.mark.asyncio
async def test_warm_teacher_null_timestamp_and_none_canonical_id():
    """Verifies that _warm_teacher handles None canonical_id and timestamp=None safely."""
    engine = SmartWarmupEngine()
    clear_schedule_caches()

    teacher_text_cache["Петров_ru"] = {
        "data": [{"day": "Среда", "time": "12:00", "subject": "History"}],
        "timestamp": None,
        "is_from_db": False,
    }

    dummy_raw = {
        "records": [{"day": "Среда", "time": "12:00", "subject": "History"}],
        "_cacheTimestamp": time.time(),
        "_fromDb": False,
    }

    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", AsyncMock(return_value=(None, "Петров", None, "Петров"))), \
         patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=dummy_raw)), \
         patch("src.database.models.teacher.Teacher.find_one", AsyncMock(return_value=None)):

        await engine._warm_teacher(None, "Петров")
        assert "Петров_ru" in teacher_text_cache
        assert "None_ru" not in teacher_text_cache

    clear_schedule_caches()


def test_add_urgent_group_and_teacher():
    """Verifies that add_urgent_group and add_urgent_teacher enroll items into priority lists without shifting cursors."""
    engine = SmartWarmupEngine()
    engine.set_top_groups([(1, "G-1"), (2, "G-2")])
    engine.set_top_teachers([(10, "T-1"), (20, "T-2")])
    engine._group_cursor = 1
    engine._teacher_cursor = 1

    engine.add_urgent_group("G-New", 55)
    assert engine._group_cursor == 1
    assert 55 in engine._priority_groups
    assert any(item[0] == 55 for item in engine._priority_groups_queue)

    engine.add_urgent_teacher("T-New", 77)
    assert engine._teacher_cursor == 1
    assert 77 in engine._priority_teachers
    assert any(item[0] == 77 for item in engine._priority_teachers_queue)






