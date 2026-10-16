"""Tests for teacher schedule warmup and search cache parity.

Verifies:
1. resolve_teacher_identity resolves both integer IDs and string names.
2. SmartSearchService.get_all_teachers attaches DB integer IDs to directory teachers.
3. Multi-key cache aliasing allows accessing warmed teacher cache via integer ID, full name, or initials.
4. SmartWarmupEngine._get_top_teachers handles string-based actions from search and includes them in warmup.
5. Inline keyboard generator emits integer IDs in callbacks for search results.
"""

from datetime import datetime, timezone, timedelta
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

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


def test_warmup_engine_timing_and_5_minute_cycle():
    """Verifies that engine warms TOP-60 teachers every 5 minutes (5.0s pacing)."""
    engine = SmartWarmupEngine()
    assert engine.TOP_TEACHERS_LIMIT == 60
    assert engine.TEACHER_INTERVAL_SEC == 5.0
    assert engine.TOP_TEACHERS_LIMIT * engine.TEACHER_INTERVAL_SEC == 300.0  # Exactly 5.0 minutes (300 seconds)


def test_user_action_ttl_index_45_days():
    """Verifies that UserAction model specifies 45-day TTL index (3,888,000s)."""
    from src.database.models.user_action import UserAction

    ttl_index = next(
        (idx for idx in UserAction.Settings.indexes if idx.document.get("expireAfterSeconds") is not None),
        None,
    )
    assert ttl_index is not None
    assert ttl_index.document["expireAfterSeconds"] == 45 * 24 * 60 * 60
    assert ttl_index.document["expireAfterSeconds"] == 3888000


@pytest.mark.asyncio
async def test_get_top_teachers_45_day_window_and_actions():
    """Verifies that UserAction aggregation enforces 45-day window and matches aiogram 3 actions."""
    engine = SmartWarmupEngine()
    captured_pipeline = []

    mock_agg = AsyncMock()
    mock_agg.to_list = AsyncMock(return_value=[{"_id": "770", "count": 10}])

    def capture_aggregate(pipeline):
        captured_pipeline.append(pipeline)
        return mock_agg

    class DummyTeacher:
        teacher_id = 770
        name = "ст.пр. Мусина С. Т."
        department = 45
        is_head = False
        job_title = ""

        def model_dump(self, *args, **kwargs):
            return {"id": 770, "teacher_id": 770, "name": "ст.пр. Мусина С. Т.", "department": 45}

    async def mock_find_one(*args, **kwargs):
        return DummyTeacher()

    with patch("src.database.models.user.User.aggregate") as mock_user_agg, \
         patch("src.database.models.user_action.UserAction.aggregate", side_effect=capture_aggregate), \
         patch("src.database.models.teacher.Teacher.find_one", side_effect=mock_find_one), \
         patch("src.database.models.teacher.Teacher.find") as mock_find_all:

        mock_user_agg.return_value.to_list = AsyncMock(return_value=[])
        mock_find_all.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

        top = await engine._get_top_teachers()
        assert len(top) >= 1
        assert top[0][0] == 770

        # Verify aggregation pipeline
        assert len(captured_pipeline) == 1
        pipe = captured_pipeline[0]
        match_stage = pipe[0]["$match"]
        assert "createdAt" in match_stage
        gte_date = match_stage["createdAt"]["$gte"]
        now_utc = datetime.now(timezone.utc)
        delta = now_utc - gte_date
        assert 44.9 <= delta.total_seconds() / (24 * 3600) <= 45.1

        # Must check aiogram 3 actions
        or_conds = match_stage["$or"]
        action_in = next(c["action"]["$in"] for c in or_conds if "$in" in c.get("action", {}))
        assert "view_teacher" in action_in
        assert "view_teacher_text" in action_in
        assert "view_teacher_image" in action_in
        assert "refresh_teacher" in action_in


@pytest.mark.asyncio
async def test_user_action_deduplication_by_entity_target():
    """Verifies that actions across multiple days (0, 1, 2) and different actions deduplicate into 1 slot."""
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

    async def mock_find_one(*args, **kwargs):
        return DummyTeacher()

    with patch("src.database.models.user.User.aggregate") as mock_user_agg, \
         patch("src.database.models.user_action.UserAction.aggregate") as mock_act_agg, \
         patch("src.database.models.teacher.Teacher.find_one", side_effect=mock_find_one), \
         patch("src.database.models.teacher.Teacher.find") as mock_find_all:

        mock_user_agg.return_value.to_list = AsyncMock(return_value=[])
        # Returns same teacher from different days and actions
        mock_act_agg.return_value.to_list = AsyncMock(return_value=[
            {"_id": "770", "count": 50},
            {"_id": "TeacherSchedule|770|0", "count": 20},
            {"_id": "TeacherSchedule|770|1", "count": 15},
            {"_id": "TeacherSchedule|770|2", "count": 10},
            {"_id": "Мусина Светлана Тахировна", "count": 8},
        ])
        mock_find_all.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

        top = await engine._get_top_teachers()
        # Only ONE entry for teacher 770 should be present
        teacher_770_entries = [t for t in top if t[0] == 770]
        assert len(teacher_770_entries) == 1

        # Musina should NOT be duplicated under string name or different key format
        musina_all_entries = [t for t in top if t[0] == 770 or "Мусина" in str(t[0]) or "Мусина" in str(t[1])]
        assert len(musina_all_entries) == 1
        assert musina_all_entries[0][0] == 770


@pytest.mark.asyncio
async def test_touch_teacher_priority_and_2h_expiry():
    """Tests touch_teacher dynamic priority queue: sets 2-hour expiry and places at front of warmup."""
    engine = SmartWarmupEngine()
    engine.TOP_TEACHERS_LIMIT = 5

    now = time.time()
    engine.touch_teacher(999, "Иванов И. И.")
    assert 999 in engine._priority_teachers
    name, exp = engine._priority_teachers[999]
    assert name == "Иванов И. И."
    assert 7190.0 <= exp - now <= 7205.0  # Exactly 2 hours (7200s)
    assert engine._teachers_cache[0] == (999, "Иванов И. И.")

    # Expired entries should be purged on next _get_top_teachers
    engine._priority_teachers[888] = ("Старый С. С.", now - 5.0)  # Expired
    with patch("src.database.models.user.User.aggregate") as mock_user_agg, \
         patch("src.database.models.user_action.UserAction.aggregate") as mock_act_agg, \
         patch("src.database.models.teacher.Teacher.find") as mock_find_all:
        mock_user_agg.return_value.to_list = AsyncMock(return_value=[])
        mock_act_agg.return_value.to_list = AsyncMock(return_value=[])
        mock_find_all.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

        top = await engine._get_top_teachers()
        # 888 must be purged
        assert 888 not in engine._priority_teachers
        # 999 must remain active and at index 0
        assert 999 in engine._priority_teachers
        assert top[0][0] == 999


@pytest.mark.asyncio
async def test_touch_teacher_invocations_in_callbacks_router():
    """Verifies that callbacks_router calls touch_teacher on text and table schedule view."""
    from src.bot.handlers.callbacks_router import (
        handle_teacher_table_image,
        handle_teacher_text_schedule,
        render_teacher_day_schedule_message,
    )
    from src.cron.cache_warmup import smart_warmup_engine

    with patch.object(smart_warmup_engine, "touch_teacher") as mock_touch, \
         patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", new_callable=AsyncMock) as mock_resolve, \
         patch("src.bot.handlers.callbacks_router.user_service.get_user_language", new_callable=AsyncMock) as mock_lang, \
         patch("src.bot.handlers.callbacks_router.get_or_fetch_teacher_text_schedule", new_callable=AsyncMock) as mock_fetch:

        mock_resolve.return_value = (770, "Мусина С. Т.", 45, "Мусина Светлана Тахировна")
        mock_lang.return_value = "ru"
        mock_fetch.return_value = {"data": [], "timestamp": time.time(), "is_from_db": False}

        # 1. Text schedule callback
        dummy_call = AsyncMock()
        dummy_call.data = "TeacherSchedule|770|0"
        dummy_call.from_user.id = 12345
        dummy_call.message = AsyncMock()
        dummy_call.message.photo = None

        await handle_teacher_text_schedule(dummy_call)
        mock_touch.assert_called_with(770, "Мусина С. Т.")

        # 2. Table schedule callback
        mock_touch.reset_mock()
        dummy_call.data = "teacherImg|770|0"
        with patch("src.bot.handlers.callbacks_router.table_image_service.render_table_image", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = b"fake_png"
            await handle_teacher_table_image(dummy_call)
            mock_touch.assert_called_with(770, "Мусина С. Т.")

        # 3. Direct render helper
        mock_touch.reset_mock()
        dummy_msg = AsyncMock()
        await render_teacher_day_schedule_message(dummy_msg, 770, 0, "ru", teacher_name="Мусина С. Т.")
        mock_touch.assert_called_with(770, "Мусина С. Т.")


@pytest.mark.asyncio
async def test_multi_key_cache_aliases_complete_parity():
    """Tests that _warm_teacher registers all aliases: ID, full name, clean name, and initials."""
    now_ts = time.time() - 30.0

    class DummyTeacher:
        teacher_id = 770
        name = "ст.пр. Мусина С. Т."
        department = 45
        is_head = False
        job_title = ""

        def model_dump(self, *args, **kwargs):
            return {"id": 770, "teacher_id": 770, "name": "ст.пр. Мусина С. Т.", "department": 45}

    async def mock_find_one(*args, **kwargs):
        return DummyTeacher()

    engine = SmartWarmupEngine()
    with patch("src.database.models.teacher.Teacher.find_one", side_effect=mock_find_one), \
         patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {
            "records": [{"day": "Понедельник", "time": "09:00-10:20", "subject": "БД", "lessonType": "лек", "group": "ИС-21-1"}],
            "_cacheTimestamp": now_ts,
            "_fromDb": False,
        }

        await engine._warm_teacher(770, "ст.пр. Мусина С. Т.")

        # Verify all alias keys in text cache
        assert "770_ru" in teacher_text_cache
        assert "770_kz" in teacher_text_cache
        assert "Мусина Светлана Тахировна_ru" in teacher_text_cache
        assert "Мусина С. Т._ru" in teacher_text_cache
        assert "Мусина С.Т._ru" in teacher_text_cache

        # Verify all alias keys in table cache
        assert "770" in teacher_table_cache
        assert 770 in teacher_table_cache
        assert "Мусина Светлана Тахировна" in teacher_table_cache
        assert "Мусина С. Т." in teacher_table_cache


@pytest.mark.asyncio
async def test_warm_teacher_numeric_id_resolution_before_api_call():
    """Tests that warming teacher by raw ID resolves name from directory before API call."""
    engine = SmartWarmupEngine()

    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", new_callable=AsyncMock) as mock_resolve, \
         patch("src.services.teacher_directory.teacher_directory_service.find_by_id") as mock_find_id, \
         patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", new_callable=AsyncMock) as mock_api:

        # Identity resolver returns ID without resolved string name
        mock_resolve.return_value = (770, "ID 770", 0, "")
        mock_find_id.return_value = {"name": "Мусина С. Т.", "fullName": "Мусина Светлана Тахировна"}
        mock_api.return_value = {"records": [], "_cacheTimestamp": time.time(), "_fromDb": False}

        await engine._warm_teacher(770, "ID 770")

        # buketov_api_service must be called with the resolved teacher name, NOT 'ID 770' or '770'
        mock_api.assert_called_once_with("Мусина С. Т.", force_refresh=True)


@pytest.mark.asyncio
async def test_touch_teacher_cache_stability_no_thrashing():
    """Tests that touch_teacher on warm cache updates priority without thrashing database timestamp."""
    engine = SmartWarmupEngine()
    engine.set_top_teachers([(111, "Преподаватель 1"), (222, "Преподаватель 2")])
    initial_ts = engine._teachers_cache_updated_at
    assert initial_ts > 0.0

    engine.touch_teacher(333, "Преподаватель 3")
    # Cache timestamp must be preserved, not reset to 0.0
    assert engine._teachers_cache_updated_at == initial_ts
    assert engine._teachers_cache[0] == (333, "Преподаватель 3")

    # _get_top_teachers should return priority teacher at front without triggering UserAction aggregation
    with patch("src.database.models.user_action.UserAction.aggregate") as mock_agg:
        top = await engine._get_top_teachers()
        assert top[0] == (333, "Преподаватель 3")
        mock_agg.assert_not_called()


