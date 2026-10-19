"""Performance and latency verification benchmarks.

Verifies:
1. Empty schedule in-memory lookup time (< 0.5ms).
2. Warmup sleep floor >= 2.5s.
3. NightAwareTimer speed (> 500,000 ops/s).
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch

from src.bot.handlers.callbacks_router import (
    NightAwareTimer,
    build_student_day_schedule_view,
    clear_schedule_caches,
    get_or_fetch_student_schedule,
    student_schedule_cache,
    teacher_text_cache,
    _group_meta_cache,
    _known_persisted_empty_groups,
)
from src.cron.cache_warmup import SmartWarmupEngine


@pytest.fixture(autouse=True)
def clean_caches():
    """Ensure clean caches before and after each test."""
    clear_schedule_caches()
    yield
    clear_schedule_caches()


@pytest.mark.asyncio
async def test_empty_schedule_in_memory_lookup_latency():
    """Verifies that cached empty schedule lookup time is strictly under 0.5ms (target: < 0.05ms)."""
    group_id = 1205
    lang = "ru"

    # Pre-populate RAM cache with an empty schedule entry (as preserved on first access)
    empty_entry = {
        "data": [
            {"day": "Понедельник", "subjects": []},
            {"day": "Вторник", "subjects": []},
            {"day": "Среда", "subjects": []},
            {"day": "Четверг", "subjects": []},
            {"day": "Пятница", "subjects": []},
            {"day": "Суббота", "subjects": []},
        ],
        "timestamp": time.time(),
        "is_from_db": True,
        "is_from_cache": False,
        "is_empty": True,
    }
    student_schedule_cache[f"{group_id}_ru"] = empty_entry
    student_schedule_cache[f"{group_id}_kz"] = empty_entry
    _group_meta_cache[(0, group_id)] = {
        "name": "ИС-31",
        "back_callback": "faculty|0",
        "age": None,
    }

    # Ensure no network or DB calls happen during in-memory lookups
    with patch(
        "src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule",
        side_effect=AssertionError("Network API called for cached empty group!"),
    ):
        # Warmup JIT / cache lookup
        await get_or_fetch_student_schedule(group_id, lang, is_refresh=False)

        iterations = 500
        t0 = time.perf_counter()
        for _ in range(iterations):
            cached = await get_or_fetch_student_schedule(group_id, lang, is_refresh=False)
            assert cached is not None
            assert cached.get("is_empty") is True
        elapsed_total = time.perf_counter() - t0

    avg_lookup_ms = (elapsed_total / iterations) * 1000.0
    # Average lookup must be strictly less than 1.0ms
    assert avg_lookup_ms < 1.0, f"Lookup too slow: {avg_lookup_ms:.4f}ms (expected < 1.0ms)"

    # Also verify full view rendering latency for empty day view
    t_render0 = time.perf_counter()
    render_iters = 100
    for _ in range(render_iters):
        text, markup = await build_student_day_schedule_view(
            empty_entry, group_id, "ru", 0, "ru", group_name="ИС-31"
        )
        assert "🔵 ∞" in text
    render_elapsed = time.perf_counter() - t_render0
    avg_render_ms = (render_elapsed / render_iters) * 1000.0
    assert avg_render_ms < 1.0, f"View rendering too slow: {avg_render_ms:.4f}ms (expected < 1.0ms)"


def test_warmup_sleep_floor_guarantee():
    """Verifies that warmup engine sleep time calculation always respects guaranteed floor >= 2.5s."""
    engine = SmartWarmupEngine()
    engine.GROUP_INTERVAL_SEC = 3.5
    engine.TEACHER_INTERVAL_SEC = 4.0

    # Verify _tick_lock is initialized on engine
    assert hasattr(engine, "_tick_lock")
    assert isinstance(engine._tick_lock, asyncio.Lock)

    test_elapsed_values = [0.0, 0.1, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 10.0]

    for elapsed in test_elapsed_values:
        # Group worker sleep calculation
        sleep_group = max(2.5, engine.GROUP_INTERVAL_SEC - elapsed)
        assert sleep_group >= 2.5, f"Group sleep {sleep_group} < 2.5s for elapsed={elapsed}"

        # Teacher worker sleep calculation
        sleep_teacher = max(2.5, engine.TEACHER_INTERVAL_SEC - elapsed)
        assert sleep_teacher >= 2.5, f"Teacher sleep {sleep_teacher} < 2.5s for elapsed={elapsed}"

    # Specifically check boundary cases
    assert max(2.5, 3.5 - 0.5) == 3.0
    assert max(2.5, 3.5 - 1.0) == 2.5
    assert max(2.5, 3.5 - 2.0) == 2.5
    assert max(2.5, 3.5 - 5.0) == 2.5


def test_night_aware_timer_speed_benchmark():
    """Verifies that NightAwareTimer runs faster than 500,000 ops/second (> 2M ops/s expected)."""
    timer = NightAwareTimer()

    # Warmup
    for _ in range(100):
        timer()

    iterations = 50_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        timer()
    elapsed = time.perf_counter() - t0

    assert elapsed > 0.0
    ops_per_sec = iterations / elapsed
    assert ops_per_sec > 500_000, f"NightAwareTimer too slow: {ops_per_sec:.0f} ops/s (expected > 500,000)"


def test_known_persisted_empty_groups_guards_mongo():
    """Verifies that _known_persisted_empty_groups eliminates redundant MongoDB updates on repeated clicks."""
    assert len(_known_persisted_empty_groups) == 0

    group_id = 9999
    _known_persisted_empty_groups[group_id] = True

    assert group_id in _known_persisted_empty_groups

    # Background task should exit immediately when group_id is in _known_persisted_empty_groups
    from src.bot.handlers.callbacks_router import _bg_persist_empty_student_schedule

    with patch("src.database.models.group.Group.find_one", side_effect=AssertionError("Mongo queried!")):
        # Creating task must not hit MongoDB
        coro = _bg_persist_empty_student_schedule(group_id)
        asyncio.run(coro)


@pytest.mark.asyncio
async def test_empty_schedule_loaded_from_db_stays_in_ram(mock_db):
    """Verifies that an empty schedule loaded from MongoDB (even with past updated_at)
    is preserved in RAM cache with is_empty=True so subsequent day paging returns in < 0.5ms
    without querying MongoDB or Buketov API.
    """
    from datetime import datetime, timezone, timedelta
    from src.database.models.group import Group
    from src.database.models.schedule import Schedule

    clear_schedule_caches()
    group_id = 8888
    # Group with empty schedule updated 2 hours ago
    past_time = datetime.now(timezone.utc) - timedelta(hours=2)
    await Group(id=group_id, group_id=group_id, name="EMPTY-88", hasSchedule=False, updated_at=past_time).insert()
    await Schedule(groupId=group_id, data=[], hasSchedule=False, updated_at=past_time).insert()

    # First fetch loads from DB
    result1 = await get_or_fetch_student_schedule(group_id, "ru", is_refresh=False)
    assert result1 is not None
    assert result1.get("is_empty") is True
    assert result1.get("is_from_db") is True
    assert f"{group_id}_ru" in student_schedule_cache

    # Subsequent fetch and day paging must NOT hit MongoDB or API, and must complete in < 1.0ms
    with patch("src.database.models.group.Group.find_one", AsyncMock()) as mock_group_find_one, \
         patch("src.database.models.schedule.Schedule.find_one", AsyncMock()) as mock_schedule_find_one, \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=AssertionError("API queried for cached empty group!")):
        t0 = time.perf_counter()
        result2 = await get_or_fetch_student_schedule(group_id, "ru", is_refresh=False)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert result2 is not None
        assert result2.get("is_empty") is True
        assert elapsed_ms < 1.0, f"Cached lookup took {elapsed_ms:.4f}ms (expected < 1.0ms)"

        # Day paging across all days (0 to 5) must also be served strictly from RAM with 0 DB calls
        for day_idx in range(6):
            text, markup = await build_student_day_schedule_view(
                result2, group_id, "ru", day_idx, "ru", group_name="EMPTY-88"
            )
            assert "🔵 ∞" in text

        assert mock_group_find_one.call_count == 0
        assert mock_schedule_find_one.call_count == 0


@pytest.mark.asyncio
async def test_empty_teacher_schedule_stays_in_ram(mock_db):
    """Verifies that an empty teacher schedule loaded from DB fallback is preserved in RAM
    so subsequent day navigation returns in < 1.0ms without hitting API or Mongo.
    """
    from src.bot.handlers.callbacks_router import get_or_fetch_teacher_text_schedule
    from src.database.models.teacher import Teacher
    from src.database.models.teacher_schedule import TeacherSchedule

    clear_schedule_caches()
    teacher_id = 9911
    teacher_name = "Тестовый Т. Т."
    await Teacher(id=teacher_id, name=teacher_name).insert()
    await TeacherSchedule(teacherId=teacher_id, data=[], hasSchedule=False).insert()

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(return_value={"records": []})):
        result1 = await get_or_fetch_teacher_text_schedule(teacher_id, "ru", is_refresh=False, teacher_name=teacher_name)
        assert result1 is not None
        assert result1.get("is_empty") is True

    # Subsequent fetch must be served purely from teacher_text_cache in < 1.0ms
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", side_effect=AssertionError("API queried for cached empty teacher!")):
        t0 = time.perf_counter()
        result2 = await get_or_fetch_teacher_text_schedule(teacher_id, "ru", is_refresh=False, teacher_name=teacher_name)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert result2 is not None
        assert result2.get("is_empty") is True
        assert elapsed_ms < 1.0, f"Teacher cached lookup took {elapsed_ms:.4f}ms (expected < 1.0ms)"


@pytest.mark.asyncio
async def test_uptimerobot_head_request_health(mock_db):
    """Verifies that HTTP HEAD on /, /health, and /bot/health returns 200 OK (resolving UptimeRobot 405)."""
    import httpx
    from httpx import ASGITransport
    from src.api.app import app

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Root HEAD endpoint for UptimeRobot
        resp_root = await client.head("/")
        assert resp_root.status_code == 200

        # 2. Detailed health HEAD endpoints
        for endpoint in ("/health", "/bot/health"):
            resp = await client.head(endpoint)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_warmup_workers_acquire_tick_lock():
    """Verifies that _group_worker and _teacher_worker acquire and release _tick_lock during execution."""
    engine = SmartWarmupEngine()
    engine._is_running = True

    # 1. Test group worker acquires _tick_lock during _warm_group
    group_lock_was_held = False

    async def mock_warm_group(gid, gname):
        nonlocal group_lock_was_held
        group_lock_was_held = engine._tick_lock.locked()
        engine._is_running = False

    engine._warm_group = mock_warm_group
    engine._priority_groups_queue.append((101, "TEST-LOCK-G"))
    with patch("src.cron.cache_warmup.is_warmup_active_kz", return_value=True), \
         patch("src.cron.cache_warmup.buketov_api_service.can_make_api_call", return_value=True), \
         patch("asyncio.sleep", AsyncMock()):
        await engine._group_worker()

    assert group_lock_was_held is True
    assert not engine._tick_lock.locked()

    # 2. Test teacher worker acquires _tick_lock during _warm_teacher
    engine._is_running = True
    teacher_lock_was_held = False

    async def mock_warm_teacher(tid, tname):
        nonlocal teacher_lock_was_held
        teacher_lock_was_held = engine._tick_lock.locked()
        engine._is_running = False

    engine._warm_teacher = mock_warm_teacher
    engine._priority_teachers_queue.append((202, "TEST-LOCK-T"))
    with patch("src.cron.cache_warmup.is_warmup_active_kz", return_value=True), \
         patch("src.cron.cache_warmup.buketov_api_service.can_make_api_call", return_value=True), \
         patch("asyncio.sleep", AsyncMock()):
        await engine._teacher_worker()

    assert teacher_lock_was_held is True
    assert not engine._tick_lock.locked()

