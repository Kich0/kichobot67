"""Tests for teacher schedule warmup and search cache parity.

Verifies:
1. resolve_teacher_identity resolves both integer IDs and string names.
2. SmartSearchService.get_all_teachers attaches DB integer IDs to directory teachers.
3. Multi-key cache aliasing allows accessing warmed teacher cache via integer ID, full name, or initials.
4. SmartWarmupEngine._get_top_teachers handles string-based actions from search and includes them in warmup.
5. Inline keyboard generator emits integer IDs in callbacks for search results.
"""

import time
import pytest
from unittest.mock import AsyncMock, patch

from src.bot.handlers.callbacks_router import (
    clear_schedule_caches,
    get_or_fetch_teacher_text_schedule,
    resolve_teacher_identity,
    teacher_table_cache,
    teacher_text_cache,
)
from src.bot.keyboards.inline import get_search_results_keyboard
from src.cron.cache_warmup import SmartWarmupEngine
from src.services.smart_search import smart_search_service


@pytest.fixture(autouse=True)
def clean_caches():
    """Ensures caches are cleared before and after each test."""
    clear_schedule_caches()
    smart_search_service.clear_cache()
    yield
    clear_schedule_caches()
    smart_search_service.clear_cache()


@pytest.mark.asyncio
async def test_resolve_teacher_identity_with_mocked_db():
    """Tests resolving string teacher name to canonical DB integer ID."""
    class DummyTeacher:
        teacher_id = 770
        name = "ст.пр. Мусина С. Т."
        department = 45
        is_head = False
        job_title = ""

        def model_dump(self, *args, **kwargs):
            return {
                "id": 770,
                "teacher_id": 770,
                "name": "ст.пр. Мусина С. Т.",
                "department": 45,
            }

    mock_doc = DummyTeacher()

    async def mock_find_one(*args, **kwargs):
        return mock_doc

    with patch("src.database.models.teacher.Teacher.find_one", side_effect=mock_find_one):
        # 1. Resolve via string name
        can_id, name, dept_id, lookup = await resolve_teacher_identity("Мусина Светлана Тахировна")
        assert can_id == 770
        assert dept_id == 45
        assert "Мусина" in name

        # 2. Resolve via numeric ID
        can_id2, name2, dept_id2, lookup2 = await resolve_teacher_identity(770)
        assert can_id2 == 770
        assert dept_id2 == 45


@pytest.mark.asyncio
async def test_multi_key_cache_hit_after_warmup():
    """Tests that warming teacher 770 allows instant cache hits when accessed via full name or initials."""
    now_ts = time.time() - 120.0  # 2 minutes ago
    sample_records = [
        {
            "day": "Понедельник",
            "time": "09:00-10:20",
            "subject": "Базы данных",
            "lessonType": "лек",
            "group": "ИС-21-1 (Ауд. 201/1)",
        }
    ]

    class DummyTeacher:
        teacher_id = 770
        name = "ст.пр. Мусина С. Т."
        department = 45
        is_head = False
        job_title = ""

        def model_dump(self, *args, **kwargs):
            return {
                "id": 770,
                "teacher_id": 770,
                "name": "ст.пр. Мусина С. Т.",
                "department": 45,
            }

    mock_doc = DummyTeacher()

    async def mock_find_one(*args, **kwargs):
        return mock_doc

    engine = SmartWarmupEngine()
    engine.TOP_TEACHERS_LIMIT = 5

    with patch("src.database.models.teacher.Teacher.find_one", side_effect=mock_find_one), \
         patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {
            "records": sample_records,
            "_cacheTimestamp": now_ts,
            "_fromDb": False,
        }

        # Warm teacher by ID
        await engine._warm_teacher(770, "ст.пр. Мусина С. Т.")

        # Cache should contain numeric and alias string keys
        assert "770_ru" in teacher_text_cache
        assert "Мусина Светлана Тахировна_ru" in teacher_text_cache or "ст.пр. Мусина С. Т._ru" in teacher_text_cache
        assert "770" in teacher_table_cache

        # Now simulate user searching and requesting via string name:
        cached = await get_or_fetch_teacher_text_schedule(
            teacher_id="Мусина Светлана Тахировна",
            ui_lang="ru",
            is_refresh=False,
            teacher_name="Мусина Светлана Тахировна",
        )

        assert cached is not None
        # Must be the warmed cache with entry_ts from 2 minutes ago, NOT live fetch now
        assert cached["timestamp"] == now_ts
        # API should NOT have been called again
        assert mock_api.call_count == 1


@pytest.mark.asyncio
async def test_search_results_keyboard_contains_integer_ids():
    """Tests that teacher buttons generated from search results use integer IDs in callbacks."""
    teachers = [
        {
            "id": 770,
            "teacher_id": 770,
            "name": "ст.пр. Мусина С. Т.",
            "fullName": "Мусина Светлана Тахировна",
        }
    ]

    kb = get_search_results_keyboard(teachers=teachers, current_day=0, tab="all")
    found_cb = False
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data and btn.callback_data.startswith("TeacherSchedule|"):
                assert btn.callback_data == "TeacherSchedule|770|0"
                found_cb = True
    assert found_cb, "Callback button with TeacherSchedule|770|0 not found"


@pytest.mark.asyncio
async def test_get_top_teachers_resolves_string_actions():
    """Tests that SmartWarmupEngine aggregates string teacher names from actions and resolves them to IDs."""
    engine = SmartWarmupEngine()
    engine.TOP_TEACHERS_LIMIT = 5

    class DummyTeacher:
        teacher_id = 770
        name = "ст.пр. Мусина С. Т."
        department = 45
        is_head = False
        job_title = ""

        def model_dump(self, *args, **kwargs):
            return {"id": 770, "teacher_id": 770, "name": "ст.пр. Мусина С. Т.", "department": 45}

    mock_doc = DummyTeacher()

    async def mock_find_one(*args, **kwargs):
        return mock_doc

    with patch("src.database.models.user.User.aggregate") as mock_user_agg, \
         patch("src.database.models.user_action.UserAction.aggregate") as mock_act_agg, \
         patch("src.database.models.teacher.Teacher.find_one", side_effect=mock_find_one), \
         patch("src.database.models.teacher.Teacher.find") as mock_find_all:

        mock_user_agg.return_value.to_list = AsyncMock(return_value=[])
        # User actions logged string teacher from search
        mock_act_agg.return_value.to_list = AsyncMock(return_value=[
            {"_id": "TeacherSchedule|Мусина Светлана Тахировна|0", "count": 25}
        ])
        mock_find_all.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

        top_teachers = await engine._get_top_teachers()
        assert len(top_teachers) >= 1
        top_ids = [t[0] for t in top_teachers]
        # Should contain resolved numeric ID 770
        assert 770 in top_ids
