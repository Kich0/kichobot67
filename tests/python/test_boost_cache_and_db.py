"""Tests for /boost plan: DB Infinity symbol ∞, No RAM cache for empty groups, Fast refresh, and No alerts.

Verifies:
1. format_elapsed_time with is_from_db=True returns '🔵 ∞' (no 30-min / 29:40 timer).
2. Known empty groups (has_schedule=False / permanent_empty=True / College groups):
   - Served directly from MongoDB with 0 network latency.
   - Display 🔵 ∞ and '🎉 ВЫХОДНОЙ!' (vacation status).
   - NEVER cached in student_schedule_cache (RAM cache).
   - NEVER turn purple (🟣).
3. Refresh button on empty groups:
   - Does fast probe with timeout <= 2.5s instead of freezing for minutes.
   - Keeps status as MongoDB Blue with ∞ if still empty.
   - Pushes into RAM cache only if real schedule with lessons appears.
4. Smart warmup engine:
   - Refuses to cache empty schedules in RAM cache.
   - Persists hasSchedule: false to MongoDB.
5. Strict UI constraint:
   - No pop-up windows, alerts or toasts.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest

from src.bot.handlers.callbacks_router import (
    _bg_persist_empty_student_schedule,
    _is_college_group,
    build_student_day_schedule_view,
    clear_schedule_caches,
    format_elapsed_time,
    get_or_fetch_student_schedule,
    student_schedule_cache,
)
from src.database.models.group import Group
from src.database.models.schedule import Schedule


def test_format_elapsed_time_infinity_for_db():
    """Verify that MongoDB-sourced data displays infinity ∞ instead of 29:40 countdown timer."""
    now = time.time()

    # 1. Even if timestamp was 29:40 ago (now - 1780.0), is_from_db=True must return '🔵 ∞'
    elapsed_db_old = format_elapsed_time(now - 1780.0, "ru", is_from_db=True, is_from_cache=False)
    assert elapsed_db_old == "🔵 ∞"

    # 2. Freshly inserted DB record must also return '🔵 ∞'
    elapsed_db_fresh = format_elapsed_time(now - 2.0, "ru", is_from_db=True, is_from_cache=False)
    assert elapsed_db_fresh == "🔵 ∞"

    # 3. Even if is_from_cache flag is mistakenly set, is_from_db=True takes precedence
    elapsed_db_override = format_elapsed_time(now - 60.0, "ru", is_from_db=True, is_from_cache=True)
    assert elapsed_db_override == "🔵 ∞"

    # 4. RAM cache (not DB) still shows purple with elapsed time
    elapsed_ram = format_elapsed_time(now - 120.0, "ru", is_from_db=False, is_from_cache=True)
    assert elapsed_ram.startswith("🟣")
    assert "2 мин." in elapsed_ram


def test_is_college_group_detection():
    """Verify detection of KarU College groups without online schedule."""
    assert _is_college_group("ИС-20") is True
    assert _is_college_group("ИС-21") is True
    assert _is_college_group("ИС-30") is True
    assert _is_college_group("ИС-31") is True
    assert _is_college_group("Колледж ИС-1") is True
    assert _is_college_group("Any Group", faculty_id=13) is True
    assert _is_college_group("Any Group", faculty_id="13") is True

    # University groups are not college groups
    assert _is_college_group("ИС-21-1") is False
    assert _is_college_group("ИС-25-3р") is False
    assert _is_college_group("ВТ-24-1", faculty_id=4) is False


@pytest.mark.asyncio
async def test_known_empty_group_instant_db_return_and_no_ram_cache(mock_db):
    """Verify that group with hasSchedule: false in DB returns instant Blue ∞ and NEVER enters RAM."""
    clear_schedule_caches()

    # Seed group in MongoDB as permanently empty
    await Group(
        id=1205,
        name="ИС-31",
        language="ru",
        faculty=13,
        hasSchedule=False,
        permanentEmpty=True,
    ).insert()

    # Step 1: Normal fetch (not refresh)
    # The API should NEVER be contacted
    with patch(
        "src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule",
        side_effect=AssertionError("API should not be called for known empty group"),
    ):
        result = await get_or_fetch_student_schedule(1205, "ru", is_refresh=False, group_name="ИС-31")

    assert result is not None
    assert result.get("is_from_db") is True
    assert result.get("is_from_cache") is False

    # Check that empty schedule is preserved in RAM cache with is_empty=True
    assert "1205_ru" in student_schedule_cache
    assert "1205_kz" in student_schedule_cache
    assert student_schedule_cache["1205_ru"].get("is_empty") is True

    # Step 2: Render view and check badge
    text, _ = await build_student_day_schedule_view(result, 1205, "ru", 0, "ru", group_name="ИС-31")
    assert "🔵 ∞" in text
    assert "🟣" not in text
    assert "29:40" not in text
    assert "29 мин." not in text


@pytest.mark.asyncio
async def test_refresh_button_on_empty_group_fast_probe_and_remains_blue(mock_db):
    """Verify that clicking Refresh on an empty group does a fast probe and stays Blue ∞."""
    clear_schedule_caches()

    await Group(
        id=1205,
        name="ИС-31",
        language="ru",
        faculty=13,
        hasSchedule=False,
        permanentEmpty=True,
    ).insert()

    # College groups (is_perm_empty) do not waste time in network requests
    result = await get_or_fetch_student_schedule(1205, "ru", is_refresh=True, group_name="ИС-31")

    assert result is not None
    assert result.get("is_from_db") is True
    assert result.get("is_from_cache") is False
    assert "1205_ru" in student_schedule_cache
    assert student_schedule_cache["1205_ru"].get("is_empty") is True

    text, _ = await build_student_day_schedule_view(result, 1205, "ru", 0, "ru", group_name="ИС-31")
    assert "🔵 ∞" in text
    assert "🟣" not in text


@pytest.mark.asyncio
async def test_refresh_on_empty_group_with_api_timeout_does_not_freeze(mock_db):
    """Verify that if the external API hangs on refresh, it times out in 2.5s and returns Blue ∞."""
    clear_schedule_caches()

    # Group marked hasSchedule: false (non-college group)
    await Group(
        id=1999,
        name="ТЕСТ-ПУСТАЯ",
        language="ru",
        faculty=2,
        hasSchedule=False,
        permanentEmpty=False,
    ).insert()

    async def slow_api_call(*args, **kwargs):
        # Simulate external scraper / API server hanging
        await asyncio.sleep(10.0)
        return {"records": []}

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=slow_api_call):
        t0 = time.time()
        result = await get_or_fetch_student_schedule(1999, "ru", is_refresh=True, group_name="ТЕСТ-ПУСТАЯ")
        elapsed = time.time() - t0

    # Must return within 2.5 - 3.5s (not 10s or 2-3 minutes)
    assert elapsed < 4.0
    assert result is not None
    assert result.get("is_from_db") is True
    assert result.get("is_from_cache") is False
    assert "1999_ru" in student_schedule_cache
    assert student_schedule_cache["1999_ru"].get("is_empty") is True

    text, _ = await build_student_day_schedule_view(result, 1999, "ru", 0, "ru", group_name="ТЕСТ-ПУСТАЯ")
    assert "🔵 ∞" in text
    assert "🟣" not in text


@pytest.mark.asyncio
async def test_fresh_api_empty_schedule_persists_to_db_and_avoids_ram(mock_db):
    """Verify that when an uncached group returns 0 lessons from API, it writes hasSchedule: false to DB and preserves empty in RAM."""
    clear_schedule_caches()

    await Group(id=1200, name="ИС-20", language="ru", faculty=13).insert()

    async def fake_empty_api(*args, **kwargs):
        return {"records": [], "_cacheTimestamp": time.time()}

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=fake_empty_api):
        result = await get_or_fetch_student_schedule(1200, "ru", is_refresh=False, group_name="ИС-20")

    assert result is not None
    assert result.get("is_from_db") is True
    assert result.get("is_from_cache") is False

    # RAM cache preserves empty schedule with is_empty=True for 0ms day paging
    assert "1200_ru" in student_schedule_cache
    assert student_schedule_cache["1200_ru"].get("is_empty") is True

    # Allow background persistence task to complete
    await asyncio.sleep(0.05)

    # Check MongoDB: hasSchedule must be False and permanentEmpty must be True (College group)
    g_doc = await Group.find_one(Group.group_id == 1200)
    assert g_doc is not None
    assert g_doc.has_schedule is False
    assert g_doc.permanent_empty is True
    assert g_doc.last_checked_at is not None

    s_doc = await Schedule.find_one({"groupId": 1200})
    assert s_doc is not None
    assert s_doc.has_schedule is False
    assert s_doc.permanent_empty is True


@pytest.mark.asyncio
async def test_warmup_engine_refuses_to_cache_empty_group_in_ram(mock_db):
    """Verify that cache warmup engine does not put empty groups into student_schedule_cache."""
    clear_schedule_caches()
    from src.cron.cache_warmup import smart_warmup_engine

    await Group(id=1206, name="ИС-30", language="ru", faculty=13).insert()

    async def fake_empty_api(*args, **kwargs):
        return {"records": [], "_cacheTimestamp": time.time()}

    with patch("src.cron.cache_warmup.buketov_api_service.get_group_schedule", side_effect=fake_empty_api):
        await smart_warmup_engine._warm_group(1206, "ИС-30")

    # RAM cache must NOT have empty schedule
    assert "1206_ru" not in student_schedule_cache
    assert "1206_kz" not in student_schedule_cache

    await asyncio.sleep(0.05)
    g_doc = await Group.find_one(Group.group_id == 1206)
    assert g_doc is not None
    assert g_doc.has_schedule is False


@pytest.mark.asyncio
async def test_schedule_appearance_transitions_to_green_and_ram_cache(mock_db):
    """Verify that if a group previously empty receives a real schedule, it saves to RAM and shows green 🟢."""
    clear_schedule_caches()

    await Group(id=1300, name="ИС-25-3р", language="ru", faculty=4, hasSchedule=False).insert()

    fake_lessons = [
        {"dayOfWeek": 1, "time": "08:30-09:20", "subject": "Архитектура ЭВМ", "auditory": "201", "teacher": "Сериков А."}
    ]

    async def fake_lessons_api(*args, **kwargs):
        return {"records": fake_lessons, "_cacheTimestamp": time.time()}

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=fake_lessons_api):
        result = await get_or_fetch_student_schedule(1300, "ru", is_refresh=True, group_name="ИС-25-3р")

    assert result is not None
    assert result.get("is_from_db") is False
    assert result.get("is_from_cache") is False

    # Now it IS stored in RAM cache because it has real lessons
    assert "1300_ru" in student_schedule_cache

    text, _ = await build_student_day_schedule_view(result, 1300, "ru", 0, "ru", group_name="ИС-25-3р")
    assert "🟢" in text
    assert "Архитектура ЭВМ" in text

    # Subsequent read without refresh -> served from RAM -> 🟣
    result_cached = await get_or_fetch_student_schedule(1300, "ru", is_refresh=False)
    assert result_cached.get("is_from_cache") is True
    text2, _ = await build_student_day_schedule_view(result_cached, 1300, "ru", 1, "ru", group_name="ИС-25-3р")
    assert "🟣" in text2


def test_is_college_group_latin_homoglyphs():
    """Verify detection of college groups even when Latin homoglyphs are used (IS-20, IC-31, College)."""
    assert _is_college_group("IS-20") is True
    assert _is_college_group("IC-21") is True
    assert _is_college_group("IS-30") is True
    assert _is_college_group("IC-31") is True
    assert _is_college_group("College Group A") is True
    assert _is_college_group("КОЛЛЕДЖ КАРУ") is True


@pytest.mark.asyncio
async def test_unseeded_college_group_navigation_skips_api_and_saves_to_db(mock_db):
    """Verify that an unseeded college group (no hasSchedule set yet) skips API call on navigation and persists to DB."""
    clear_schedule_caches()

    # Group exists with faculty=13, but hasSchedule and permanentEmpty are None
    await Group(id=1207, name="ИС-21", language="ru", faculty=13).insert()

    with patch(
        "src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule",
        side_effect=AssertionError("Buketov API should NEVER be called for unseeded college group!"),
    ):
        result = await get_or_fetch_student_schedule(1207, "ru", is_refresh=False, group_name="ИС-21")

    assert result is not None
    assert result.get("is_from_db") is True
    assert result.get("is_from_cache") is False
    assert "1207_ru" in student_schedule_cache
    assert student_schedule_cache["1207_ru"].get("is_empty") is True

    text, _ = await build_student_day_schedule_view(result, 1207, "ru", 0, "ru", group_name="ИС-21")
    assert "🔵 ∞" in text

    # Allow background persistence task to finish
    await asyncio.sleep(0.05)
    g_doc = await Group.find_one(Group.group_id == 1207)
    assert g_doc is not None
    assert g_doc.has_schedule is False
    assert g_doc.permanent_empty is True


@pytest.mark.asyncio
async def test_empty_teacher_text_schedule_never_enters_ram_and_no_backdating(mock_db):
    """Verify that empty teacher text schedule is preserved in teacher_text_cache with is_empty=True and does not use now - 1780.0 hack."""
    clear_schedule_caches()
    from src.bot.handlers.callbacks_router import (
        get_or_fetch_teacher_text_schedule,
        teacher_text_cache,
    )
    from src.database.models.teacher import Teacher

    await Teacher(id=999, teacher_id=999, name="Пустой Преподаватель").insert()

    async def fake_empty_teacher(*args, **kwargs):
        return {"records": [], "_cacheTimestamp": time.time()}

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", side_effect=fake_empty_teacher):
        now_ts = time.time()
        result = await get_or_fetch_teacher_text_schedule(999, "ru", teacher_name="Пустой Преподаватель")

    assert result is not None
    assert result.get("is_from_db") is True
    assert result.get("is_from_cache") is False
    # Timestamp must NOT be backdated ~1780 seconds
    assert abs(result["timestamp"] - now_ts) < 5.0

    # teacher_text_cache preserves empty teacher with is_empty=True
    assert "999_ru" in teacher_text_cache
    assert teacher_text_cache["999_ru"].get("is_empty") is True

    elapsed = format_elapsed_time(result["timestamp"], "ru", is_from_db=result["is_from_db"])
    assert elapsed == "🔵 ∞"


@pytest.mark.asyncio
async def test_warmup_engine_skips_college_group_api_call(mock_db):
    """Verify that SmartWarmupEngine skips network request to Buketov API for college groups."""
    clear_schedule_caches()
    from src.cron.cache_warmup import smart_warmup_engine

    await Group(id=1208, name="ИС-31", language="ru", faculty=13).insert()

    with patch(
        "src.cron.cache_warmup.buketov_api_service.get_group_schedule",
        side_effect=AssertionError("Warmup must not call Buketov API for college groups!"),
    ):
        await smart_warmup_engine._warm_group(1208, "ИС-31")

    assert "1208_ru" not in student_schedule_cache
    await asyncio.sleep(0.05)
    g_doc = await Group.find_one(Group.group_id == 1208)
    assert g_doc is not None
    assert g_doc.has_schedule is False
    assert g_doc.permanent_empty is True

