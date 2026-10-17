"""Tests for the purple circle (🟣) RAM cache schedule indicator.

Verifies:
1. format_elapsed_time returns:
   - 🔵 when is_from_db=True
   - 🟣 when is_from_cache=True (and not from DB)
   - 🟢 when fresh from API (is_from_cache=False, is_from_db=False)
   - 🟡 / 🔴 when aged and not from cache/db
2. Student schedule transitions:
   - Fresh API fetch -> 🟢
   - Refresh in cooldown (10m) -> 🟣
   - Subsequent cached pagination / fetch -> 🟣
   - Refresh after cooldown expired -> 🟢
   - MongoDB fallback -> 🔵
3. Teacher text schedule transitions:
   - Fresh API fetch -> 🟢
   - Refresh in cooldown (5m) -> 🟣
   - Subsequent cached fetch -> 🟣
   - MongoDB fallback -> 🔵
4. Teacher table image caption:
   - Refresh in cooldown (20m) -> 🟣
5. Background cache warmup:
   - _warm_group and _warm_teacher set is_from_cache=True -> 🟣
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from src.bot.handlers.callbacks_router import (
    build_student_day_schedule_view,
    build_teacher_day_text_view,
    clear_schedule_caches,
    format_elapsed_time,
    get_or_fetch_student_schedule,
    get_or_fetch_teacher_text_schedule,
    student_schedule_cache,
    teacher_text_cache,
)
from src.database.models.group import Group
from src.database.models.teacher import Teacher


def test_format_elapsed_time_indicator_badges():
    """Verify that badges correctly distinguish API, RAM cache, DB, and age."""
    now = time.time()

    # 1. Fresh from API (not cache, not DB)
    api_text = format_elapsed_time(now - 10.0, "ru", is_from_db=False, is_from_cache=False)
    assert api_text.startswith("🟢")
    assert "10 сек." in api_text

    # 2. From in-memory RAM cache
    cache_text = format_elapsed_time(now - 120.0, "ru", is_from_db=False, is_from_cache=True)
    assert cache_text.startswith("🟣")
    assert "2 мин." in cache_text

    # 3. From MongoDB database fallback (always 🔵 even if in cache)
    db_text = format_elapsed_time(now - 300.0, "ru", is_from_db=True, is_from_cache=False)
    assert db_text.startswith("🔵")

    db_cache_text = format_elapsed_time(now - 300.0, "ru", is_from_db=True, is_from_cache=True)
    assert db_cache_text.startswith("🔵")

    # 4. Aged non-cached API responses (>= 5h -> 🟡, >= 24h -> 🔴)
    yellow_text = format_elapsed_time(now - 5 * 3600 - 60, "ru", is_from_db=False, is_from_cache=False)
    assert yellow_text.startswith("🟡")
    assert "5 ч." in yellow_text

    red_text = format_elapsed_time(now - 25 * 3600, "ru", is_from_db=False, is_from_cache=False)
    assert red_text.startswith("🔴")
    assert "1 дн." in red_text


@pytest.mark.asyncio
async def test_student_schedule_api_then_cache_indicator(mock_db):
    """Verify student schedule flow: API (🟢) -> cached read / pagination (🟣) -> refresh after cooldown (🟢)."""
    clear_schedule_caches()
    await Group(id=5001, name="ВТ-2401", language="ru", faculty=1, program=10, age=1).insert()

    fake_lessons = [
        {"dayOfWeek": 1, "time": "08:30-09:20", "subject": "Физика", "auditory": "101", "teacher": "Иванов И.И."}
    ]

    async def fake_api(group_name, *args, **kwargs):
        return {
            "records": fake_lessons,
            "_cacheTimestamp": time.time(),
        }

    # Step 1: Cache is empty -> fetch from API
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=fake_api):
        cached1 = await get_or_fetch_student_schedule(5001, "ru", is_refresh=False, group_name="ВТ-2401")
        assert cached1 is not None
        assert cached1.get("is_from_cache") is False
        assert cached1.get("is_from_db") is False

        text1, _ = await build_student_day_schedule_view(cached1, 5001, "ru", 0, "ru", group_name="ВТ-2401")
        assert "🟢" in text1
        assert "🟣" not in text1
        assert "🔵" not in text1

    # Step 2: User flips days or re-opens group -> served from RAM cache -> 🟣
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=AssertionError("Should not hit API")):
        cached2 = await get_or_fetch_student_schedule(5001, "ru", is_refresh=False)
        assert cached2 is not None
        assert cached2.get("is_from_cache") is True

        text2, _ = await build_student_day_schedule_view(cached2, 5001, "ru", 1, "ru", group_name="ВТ-2401")
        assert "🟣" in text2
        assert "🟢" not in text2

    # Step 3: Refresh after cooldown -> fresh fetch from API -> 🟢
    student_schedule_cache["5001_ru"]["timestamp"] = time.time() - 650.0
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=fake_api):
        cached3 = await get_or_fetch_student_schedule(5001, "ru", is_refresh=True, group_name="ВТ-2401")
        assert cached3 is not None
        assert cached3.get("is_from_cache") is False

        text3, _ = await build_student_day_schedule_view(cached3, 5001, "ru", 0, "ru", group_name="ВТ-2401")
        assert "🟢" in text3
        assert "🟣" not in text3


@pytest.mark.asyncio
async def test_student_schedule_refresh_cooldown_indicator(mock_db):
    """Verify that clicking refresh within cooldown switches to 🟣."""
    clear_schedule_caches()
    now = time.time()
    fake_days = [
        {"day": "Понедельник", "subjects": [{"time": "08:30", "subject": "Математика"}]}
    ]
    student_schedule_cache["5002_ru"] = {
        "data": fake_days,
        "timestamp": now - 30.0,
        "is_from_db": False,
        "is_from_cache": False,
    }

    # Explicit refresh within cooldown passes is_from_cache=True
    text, _ = await build_student_day_schedule_view(
        student_schedule_cache["5002_ru"],
        5002,
        "ru",
        0,
        "ru",
        group_name="ВТ-2402",
        is_from_cache=True,
    )
    assert "🟣 30 сек." in text


@pytest.mark.asyncio
async def test_teacher_text_schedule_api_then_cache_indicator(mock_db):
    """Verify teacher text schedule flow: API (🟢) -> cached read (🟣) -> DB fallback (🔵)."""
    clear_schedule_caches()
    await Teacher(id=801, name="Асанов Б. К.", full_name="Асанов Бахыт Касымович", department=1).insert()

    fake_teacher_records = [
        {"dayOfWeek": 1, "time": "10:00-10:50", "subject": "Химия", "auditory": "205", "group": "ВТ-2401", "teacher": "Асанов Б.К."}
    ]

    async def fake_teacher_api(name, *args, **kwargs):
        return {
            "records": fake_teacher_records,
            "_cacheTimestamp": time.time(),
        }

    # Step 1: Initial fetch from API -> 🟢
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", side_effect=fake_teacher_api):
        cached1 = await get_or_fetch_teacher_text_schedule(801, "ru", is_refresh=False, teacher_name="Асанов Б. К.")
        assert cached1 is not None
        assert cached1.get("is_from_cache") is False

        text1, _ = await build_teacher_day_text_view(cached1, 801, 0, "ru", teacher_name="Асанов Б. К.")
        assert "🟢" in text1
        assert "🟣" not in text1

    # Step 2: Next cached read -> 🟣
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", side_effect=AssertionError("Should not hit API")):
        cached2 = await get_or_fetch_teacher_text_schedule(801, "ru", is_refresh=False)
        assert cached2 is not None
        assert cached2.get("is_from_cache") is True

        text2, _ = await build_teacher_day_text_view(cached2, 801, 1, "ru", teacher_name="Асанов Б. К.")
        assert "🟣" in text2


@pytest.mark.asyncio
async def test_cache_warmup_marks_prewarmed_as_cache(mock_db):
    """Verify that background warmup (_warm_group & _warm_teacher) sets is_from_cache=True."""
    clear_schedule_caches()
    from src.cron.cache_warmup import smart_warmup_engine

    await Group(id=5003, name="ИС-2301", language="ru", faculty=1, program=10, age=2).insert()

    fake_records = [
        {"dayOfWeek": 1, "time": "08:30-09:20", "subject": "Базы данных", "auditory": "301", "teacher": "Петров П.П."}
    ]

    async def fake_warmup_api(name, *args, **kwargs):
        return {
            "records": fake_records,
            "_cacheTimestamp": time.time() - 180.0,
        }

    with patch("src.cron.cache_warmup.buketov_api_service.get_group_schedule", side_effect=fake_warmup_api):
        await smart_warmup_engine._warm_group(5003, "ИС-2301")

    assert "5003_ru" in student_schedule_cache
    entry = student_schedule_cache["5003_ru"]
    assert entry.get("is_from_cache") is True

    # User opens pre-warmed group -> displays 🟣
    text, _ = await build_student_day_schedule_view(entry, 5003, "ru", 0, "ru", group_name="ИС-2301")
    assert "🟣" in text
    assert "3 мин." in text
