"""Unit and integration tests for Phase 6 Cron Jobs and APScheduler."""

from __future__ import annotations

import asyncio
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
    assert engine.GROUP_INTERVAL_SEC == 4.0
    assert engine.TEACHER_INTERVAL_SEC == 5.0
    assert engine.TOP_GROUPS_LIMIT == 150
    assert engine.TOP_TEACHERS_LIMIT == 60
    assert engine.HEADROOM == 5

    group_req_per_min = 60.0 / engine.GROUP_INTERVAL_SEC  # 15 req/min
    teacher_req_per_min = 60.0 / engine.TEACHER_INTERVAL_SEC  # 12 req/min
    total_req_per_min = group_req_per_min + teacher_req_per_min  # 27 req/min

    assert total_req_per_min == 27.0
    # Must consume around 24.5% of university 110 req/min limit, leaving 75.5% free
    assert total_req_per_min <= 110.0 * 0.25


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



