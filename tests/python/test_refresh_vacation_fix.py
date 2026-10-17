"""Tests for Refresh Button & Vacation Bug Fixes.

Compatible with both pytest and python -m unittest.

Verifies:
1. `_has_any_lessons` accurately detects whether adapted schedule contains actual lessons.
2. `clean_titles` and `_extract_surname` properly strip master's degrees (м.п.н., магистр, etc.)
   and punctuation without misidentifying degree abbreviations as initials or surnames.
3. `touch_group` and `touch_teacher` refuse to poison priority warmup queues with stubs
   like 'Группа <id>' or 'ID <num>'.
4. `_safe_teacher_cb_id` preserves initials even when truncating long names to Telegram 64-byte callback limits.
5. Schedule cache preservation: empty API response (records=[]) does not overwrite
   an existing valid cache with 6 days of 'Выходной'.
"""

from __future__ import annotations

import time
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from src.bot.handlers.callbacks_router import (
    _has_any_lessons,
    clear_schedule_caches,
    get_or_fetch_student_schedule,
    get_or_fetch_teacher_text_schedule,
    student_schedule_cache,
    teacher_text_cache,
)
from src.bot.keyboards.inline import _safe_teacher_cb_id
from src.cron.cache_warmup import SmartWarmupEngine
from src.services.buketov_api import BuketovApiService
from src.services.schedule_adapter import ScheduleApiAdapter
from src.services.teacher_directory import clean_titles


class TestHasAnyLessons(unittest.TestCase):
    """Verifies _has_any_lessons distinguishes real schedules from empty 6-day stubs."""

    def test_empty_input(self):
        self.assertFalse(_has_any_lessons([]))
        self.assertFalse(_has_any_lessons(None))
        self.assertFalse(_has_any_lessons("invalid"))

    def test_six_vacation_days_returns_false(self):
        adapter = ScheduleApiAdapter()
        # When API returns 0 records, adapter returns 6 empty days (Mon-Sat)
        adapted_student = adapter.adapt_group_schedule([], "ru")
        self.assertEqual(len(adapted_student), 6)
        self.assertFalse(_has_any_lessons(adapted_student))

        adapted_teacher = adapter.adapt_teacher_schedule([], "ru")
        self.assertEqual(len(adapted_teacher), 6)
        self.assertFalse(_has_any_lessons(adapted_teacher))

    def test_student_with_lesson_returns_true(self):
        schedule = [
            {"day": "Понедельник", "subjects": []},
            {
                "day": "Вторник",
                "subjects": [
                    {
                        "time": "08:30 - 09:20",
                        "subject": "Высшая математика",
                        "type": "лек.",
                        "teacher": "Иванов И.И.",
                        "auditorium": "205",
                    }
                ],
            },
            {"day": "Среда", "subjects": []},
        ]
        self.assertTrue(_has_any_lessons(schedule))

    def test_teacher_with_lesson_returns_true(self):
        schedule = [
            {"day": "Понедельник", "groups": []},
            {
                "day": "Вторник",
                "groups": [
                    {
                        "time": "10:00 - 11:20",
                        "group": "ИС-21-1",
                        "subject": "Базы данных",
                        "lessonType": "лаб.",
                        "auditorium": "301",
                    }
                ],
            },
        ]
        self.assertTrue(_has_any_lessons(schedule))


class TestAcademicDegreeStripping(unittest.TestCase):
    """Verifies clean_titles and _extract_surname handle master's degrees and punctuation."""

    def test_clean_titles_master_degrees(self):
        self.assertEqual(clean_titles("м.п.н. Искакова А.Б."), "Искакова А.Б.")
        self.assertEqual(clean_titles("м.э.н., доцент Касымова Г.С."), "Касымова Г.С.")
        self.assertEqual(clean_titles("м.ю.н. Ахметов Е.К."), "Ахметов Е.К.")
        self.assertEqual(clean_titles("м.т.н., сениор-лектор Сейткалиев Н.А."), "Сейткалиев Н.А.")
        self.assertEqual(clean_titles("магистр Бакирова Д.М."), "Бакирова Д.М.")
        self.assertEqual(clean_titles("зав. каф., доцент Оспанов Б.К."), "Оспанов Б.К.")

    def test_extract_surname_with_academic_degrees(self):
        api = BuketovApiService()
        # Should not extract 'м.' or 'п.' or 'Б.' as the surname
        self.assertEqual(api._extract_surname("м.п.н. Искакова А.Б."), "Искакова")
        self.assertEqual(api._extract_surname("м.э.н., Касымова Г.С."), "Касымова")
        self.assertEqual(api._extract_surname("сениор-лектор, магистр Омаров Д.Ж."), "Омаров")
        self.assertEqual(api._extract_surname("зав. каф. Досмагамбетова А.К."), "Досмагамбетова")


class TestWarmupEngineNoStubPoisoning(unittest.TestCase):
    """Verifies that touch_group and touch_teacher never poison warmup caches with placeholder stubs."""

    def test_touch_group_rejects_stub(self):
        engine = SmartWarmupEngine()
        engine._groups_cache = [(101, "ФИЗ-21-1")]
        engine._priority_groups = {101: ("ФИЗ-21-1", time.time() + 7200)}

        # Attempt to touch with stub
        engine.touch_group(101, "Группа 101")
        # Real name must be preserved, not overwritten with 'Группа 101'
        self.assertEqual(engine._groups_cache[0], (101, "ФИЗ-21-1"))
        self.assertEqual(engine._priority_groups[101][0], "ФИЗ-21-1")

    def test_touch_group_without_name_does_not_poison_empty(self):
        engine = SmartWarmupEngine()
        engine._groups_cache = []
        engine._priority_groups = {}

        # If group name not found in meta-cache, should not inject 'Группа 999' into priority
        engine.touch_group(999, "")
        self.assertNotIn(999, engine._priority_groups)

    def test_touch_teacher_rejects_stub(self):
        engine = SmartWarmupEngine()
        engine._teachers_cache = [(55, "Ахметов С.К.")]
        engine._priority_teachers = {55: ("Ахметов С.К.", time.time() + 7200)}

        # Attempt to touch with bare ID stub
        engine.touch_teacher(55, "ID 55")
        self.assertEqual(engine._priority_teachers[55][0], "Ахметов С.К.")

        # Touching an unknown ID with stub should be ignored, not added as '55'
        engine.touch_teacher(999, "ID 999")
        self.assertNotIn(999, engine._priority_teachers)


class TestSafeTeacherCallbackId(unittest.TestCase):
    """Verifies _safe_teacher_cb_id respects the Telegram 64-byte limit without cutting off initials."""

    def test_numeric_id_unchanged(self):
        self.assertEqual(_safe_teacher_cb_id(123), 123)
        self.assertEqual(_safe_teacher_cb_id("456"), 456)

    def test_short_name_preserves_initials(self):
        res = _safe_teacher_cb_id("Иванов И.И.")
        self.assertEqual(res, "Иванов И.И.")
        self.assertLessEqual(len(str(res).encode("utf-8")), 38)

    def test_long_hyphenated_surname_preserves_initials(self):
        # Long name that exceeds 38 bytes
        long_name = "Баймұхамедова-Шаяхметова Айгүл Бақытқызы"
        res = _safe_teacher_cb_id(long_name)
        res_str = str(res)
        # Must fit within 38 bytes
        self.assertLessEqual(len(res_str.encode("utf-8")), 38)
        # Initials must NOT be stripped or cut off
        self.assertTrue(
            res_str.endswith(".Б.") or res_str.endswith(".Б") or "А.Б." in res_str or "А.Б" in res_str
        )


class TestScheduleCachePreservationOnEmptyApi(unittest.IsolatedAsyncioTestCase):
    """Verifies that empty API results (records=[]) do not overwrite valid cached schedules."""

    async def test_student_refresh_empty_api_preserves_valid_cache(self):
        clear_schedule_caches()
        group_id = 9999
        now = time.time()

        # Seed valid cache
        valid_days = [
            {"day": "Понедельник", "subjects": [{"subject": "Физика", "time": "08:30"}]},
            {"day": "Вторник", "subjects": []},
            {"day": "Среда", "subjects": []},
            {"day": "Четверг", "subjects": []},
            {"day": "Пятница", "subjects": []},
            {"day": "Суббота", "subjects": []},
        ]
        student_schedule_cache[f"{group_id}_ru"] = {
            "data": valid_days,
            "timestamp": now - 300,
            "is_from_db": False,
        }

        # Mock API to return empty records (0 lessons)
        with patch("src.services.buketov_api.buketov_api_service.get_group_schedule", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"records": [], "_fromDb": False}

            # Fetch with is_refresh=True
            res = await get_or_fetch_student_schedule(group_id, "ru", is_refresh=True, group_name="ТЕСТ-99")

            # Must return the valid schedule, not empty vacation days
            self.assertIsNotNone(res)
            self.assertTrue(_has_any_lessons(res["data"]))
            self.assertEqual(res["data"][0]["subjects"][0]["subject"], "Физика")

        clear_schedule_caches()

    async def test_teacher_refresh_empty_api_preserves_valid_cache(self):
        clear_schedule_caches()
        teacher_id = 8888
        now = time.time()

        valid_days = [
            {"day": "Понедельник", "groups": [{"group": "ИС-21", "subject": "Программирование"}]},
            {"day": "Вторник", "groups": []},
            {"day": "Среда", "groups": []},
            {"day": "Четверг", "groups": []},
            {"day": "Пятница", "groups": []},
            {"day": "Суббота", "groups": []},
        ]
        teacher_text_cache[f"{teacher_id}_ru"] = {
            "data": valid_days,
            "timestamp": now - 300,
            "is_from_db": False,
        }

        with patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"records": [], "_fromDb": False}

            res = await get_or_fetch_teacher_text_schedule(teacher_id, "ru", is_refresh=True, teacher_name="Тестов Т.Т.")

            self.assertIsNotNone(res)
            self.assertTrue(_has_any_lessons(res["data"]))
            self.assertEqual(res["data"][0]["groups"][0]["group"], "ИС-21")

        clear_schedule_caches()


if __name__ == "__main__":
    unittest.main()
