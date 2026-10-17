"""Unit tests for photo callback navigation, black_list_service O(1) checks, and performance optimizations.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, User

from src.bot.handlers.callbacks_router import (
    handle_department_menu,
    handle_teacher_menu,
    safe_edit_or_replace_message,
    student_schedule_cache,
    teacher_table_cache,
    teacher_text_cache,
)
from src.cron.memory_trim import trim_process_memory
from src.services.black_list_service import black_list_service


def make_photo_msg(user_id=99200):
    u = User(id=user_id, is_bot=False, first_name="Тест", username="phototester")
    c = Chat(id=user_id, type="private")
    photo = [
        PhotoSize(file_id="photo_123", file_unique_id="uniq_123", width=100, height=100)
    ]
    msg = Message(
        message_id=99,
        date=datetime.now(timezone.utc),
        chat=c,
        from_user=u,
        photo=photo,
    )
    object.__setattr__(msg, "answer", AsyncMock())
    object.__setattr__(msg, "delete", AsyncMock())
    object.__setattr__(msg, "edit_text", AsyncMock())
    object.__setattr__(msg, "_bot", AsyncMock())
    return msg


def make_text_msg(user_id=99200, text="Hello"):
    u = User(id=user_id, is_bot=False, first_name="Тест", username="texttester")
    c = Chat(id=user_id, type="private")
    msg = Message(
        message_id=98,
        date=datetime.now(timezone.utc),
        chat=c,
        from_user=u,
        text=text,
    )
    object.__setattr__(msg, "answer", AsyncMock())
    object.__setattr__(msg, "delete", AsyncMock())
    object.__setattr__(msg, "edit_text", AsyncMock())
    object.__setattr__(msg, "_bot", AsyncMock())
    return msg


@pytest.mark.asyncio
async def test_safe_edit_or_replace_on_photo_message():
    """Verify that photo messages are deleted and new text message is answered."""
    msg = make_photo_msg()
    await safe_edit_or_replace_message(msg, "Новый текст", reply_markup=None)

    # edit_text must not be called on photo messages
    assert msg.edit_text.call_count == 0
    # delete must be called
    assert msg.delete.call_count == 1
    # answer must be called with the new text
    assert msg.answer.call_count == 1
    assert "Новый текст" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_safe_edit_or_replace_on_text_message():
    """Verify that text messages are smoothly edited in place."""
    msg = make_text_msg()
    await safe_edit_or_replace_message(msg, "Обновленный текст", reply_markup=None)

    assert msg.edit_text.call_count == 1
    assert "Обновленный текст" in msg.edit_text.call_args[0][0]
    assert msg.delete.call_count == 0
    assert msg.answer.call_count == 0


@pytest.mark.asyncio
async def test_safe_edit_or_replace_fallback_on_bad_request():
    """Verify that if edit_text fails with TelegramBadRequest, message is deleted and answered."""
    msg = make_text_msg()
    msg.edit_text.side_effect = TelegramBadRequest(
        method="editMessageText",
        message="Bad Request: there is no text in the message to edit",
    )

    await safe_edit_or_replace_message(msg, "Резервный текст", reply_markup=None)

    assert msg.delete.call_count == 1
    assert msg.answer.call_count == 1
    assert "Резервный текст" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_department_menu_from_photo_message(mock_db):
    """Verify that clicking back in teacher table photo message returns to department menu cleanly."""
    photo_msg = make_photo_msg()
    call = CallbackQuery(
        id="call_dept_back",
        from_user=photo_msg.from_user,
        chat_instance="inst_dept",
        message=photo_msg,
        data="department|0",
    )
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", photo_msg.bot)

    await handle_department_menu(call)

    assert call.answer.call_count == 1
    # Photo message deleted
    assert photo_msg.delete.call_count == 1
    # New department menu sent via answer
    assert photo_msg.answer.call_count == 1
    assert "Выбор кафедры" in photo_msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_teacher_menu_from_photo_message(mock_db):
    """Verify that clicking back from teacher table photo message returns to teacher catalog."""
    photo_msg = make_photo_msg()
    call = CallbackQuery(
        id="call_tch_back",
        from_user=photo_msg.from_user,
        chat_instance="inst_tch",
        message=photo_msg,
        data="teacher|101|0",
    )
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", photo_msg.bot)

    await handle_teacher_menu(call)

    assert call.answer.call_count == 1
    # Photo message deleted
    assert photo_msg.delete.call_count == 1
    # New teachers catalog sent via answer
    assert photo_msg.answer.call_count == 1
    assert "Выбор преподавателя" in photo_msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_black_list_service_crud_and_expiration(mock_db):
    """Verify BlackListService add_ban, add_mute, is_banned, is_muted, remove, and auto-expiration."""
    black_list_service.clear_cache()
    uid = 99301

    assert not black_list_service.is_banned(uid)
    assert not black_list_service.is_muted(uid)

    # 1. Add permanent ban
    await black_list_service.add_ban(uid, reason="Spamming", username="spammer")
    assert black_list_service.is_banned(uid)
    assert not black_list_service.is_muted(uid)

    rec = black_list_service.get_restriction(uid)
    assert rec is not None
    assert rec.type == "ban"
    assert rec.reason == "Spamming"

    # 2. Remove ban
    removed = await black_list_service.remove(uid)
    assert removed is True
    assert not black_list_service.is_banned(uid)

    # 3. Add temporary mute with future expiration
    future_time = datetime.now(timezone.utc) + timedelta(minutes=10)
    await black_list_service.add_mute(uid, until=future_time, reason="Flood")
    assert black_list_service.is_muted(uid)
    assert not black_list_service.is_banned(uid)

    # 4. Auto-expiration for past until
    past_time = datetime.now(timezone.utc) - timedelta(seconds=5)
    await black_list_service.add_mute(uid, until=past_time, reason="Old")
    # Immediate check should detect expiration and return False
    assert not black_list_service.is_muted(uid)


def test_schedule_ttl_caches_capacity():
    """Verify that schedule viewing caches are bounded TTLCaches preventing unbounded RAM leaks."""
    assert student_schedule_cache.maxsize >= 360
    assert student_schedule_cache.ttl == 1800.0

    assert teacher_text_cache.maxsize >= 720
    assert teacher_text_cache.ttl == 1800.0

    assert teacher_table_cache.maxsize >= 100
    assert teacher_table_cache.ttl == 1200.0


def test_memory_trim_execution():
    """Verify that trim_process_memory runs cleanly."""
    # Should execute garbage collection without exceptions
    trim_process_memory()


def test_search_keyboards_teacher_text_schedule_and_directory_fallback():
    """Verify search and fuzzy keyboards open text schedule (TeacherSchedule) and handle id=0 teachers."""
    from src.bot.keyboards.inline import get_fuzzy_suggestions_keyboard, get_search_results_keyboard

    teachers = [
        {"id": 1001, "name": "Смагулов А.К.", "fullName": "Смагулов Аскар Каирович"},
        {"id": 0, "name": "Директориев Б.С.", "fullName": "Директориев Берик Серикович"},
    ]
    kb = get_search_results_keyboard(teachers=teachers, lang="ru", current_day=3)
    assert kb.inline_keyboard[0][0].callback_data == "TeacherSchedule|1001|3"
    assert kb.inline_keyboard[1][0].callback_data == "TeacherSchedule|Директориев Б.С.|3"

    suggestions = [
        {"type": "teacher", "item": {"id": 0, "name": "Оспанов Е.Т.", "fullName": "Оспанов Ерлан Талгатович"}},
        {"type": "group", "item": {"id": 3001, "name": "ИСиТ-2301", "language": "ru"}},
    ]
    f_kb = get_fuzzy_suggestions_keyboard(suggestions=suggestions, lang="ru", current_day=4)
    assert f_kb.inline_keyboard[0][0].callback_data == "TeacherSchedule|Оспанов Е.Т.|4"
    assert f_kb.inline_keyboard[1][0].callback_data == "chooseScheduleLanguage|ru|3001|4"


@pytest.mark.asyncio
async def test_warmup_timestamp_preserved_and_zero_db_pagination(mock_db):
    """Verify _cacheTimestamp from warmup is preserved and pagination uses _group_meta_cache without DB hits."""
    import time
    from src.bot.handlers.callbacks_router import (
        _group_meta_cache,
        _norm_cache_lang,
        build_student_day_schedule_view,
        clear_schedule_caches,
        get_or_fetch_student_schedule,
    )
    from src.database.models.group import Group

    clear_schedule_caches()
    await Group(id=3001, name="ИСиТ-2301", language="ru", faculty=10, program=100, age=2).insert()

    assert _norm_cache_lang("рус") == "ru"
    assert _norm_cache_lang("каз") == "kz"

    warm_ts = time.time() - 420.0  # 7 minutes ago
    fake_api_data = [
        {"dayOfWeek": 1, "time": "09:00-09:50", "subject": "Алгоритмы", "auditory": "301", "teacher": "Смагулов А.К."}
    ]

    async def fake_get_group_schedule(group_name, *args, **kwargs):
        return {
            "records": fake_api_data,
            "_cacheTimestamp": warm_ts,
        }

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=fake_get_group_schedule):
        cached = await get_or_fetch_student_schedule(
            group_id=3001,
            sch_lang="рус",
            is_refresh=False,
            group_name="ИСиТ-2301",
        )
        assert cached is not None
        text1, _ = await build_student_day_schedule_view(
            cached=cached,
            group_id=3001,
            sch_lang="рус",
            day_num=0,
            ui_lang="ru",
            group_name="ИСиТ-2301",
        )

    assert "🟢 7 мин." in text1 or "🟢 6 мин." in text1
    assert "3001_ru" in student_schedule_cache
    assert abs(student_schedule_cache["3001_ru"]["timestamp"] - warm_ts) < 1.0
    assert any(k[1] == 3001 for k in _group_meta_cache)

    # Now paginate to day 1 with Group.find_one patched to fail if called (verifying 0 DB queries)
    with patch("src.database.models.group.Group.find_one", side_effect=AssertionError("DB should not be queried on cached pagination")):
        cached2 = await get_or_fetch_student_schedule(
            group_id=3001,
            sch_lang="ru",
            is_refresh=False,
        )
        text2, _ = await build_student_day_schedule_view(
            cached=cached2,
            group_id=3001,
            sch_lang="ru",
            day_num=1,
            ui_lang="ru",
        )
    assert "ИСиТ-2301" in text2


@pytest.mark.asyncio
async def test_bg_persist_schedule_hash_deduplication(mock_db):
    """Verify _bg_persist_student_schedule skips DB writes when schedule content hash is unchanged."""
    from src.bot.handlers.callbacks_router import (
        _bg_persist_student_schedule,
        _persisted_student_hashes,
        clear_schedule_caches,
    )

    clear_schedule_caches()
    days_data = [{"day": "Понедельник", "subjects": [{"time": "09:00", "subject": "Математика"}]}]

    await _bg_persist_student_schedule(3001, days_data)
    assert any(k[1] == 3001 for k in _persisted_student_hashes)

    with patch("src.database.models.schedule.Schedule.find_one", side_effect=AssertionError("Should skip DB when hash matches")):
        await _bg_persist_student_schedule(3001, days_data)


@pytest.mark.asyncio
async def test_health_monitor_get_status_and_api_health_check():
    """Verify health_monitor.get_status() and buketov_api_service.health_check() work for /info and /test."""
    from src.api.health import health_monitor
    from src.services.buketov_api import buketov_api_service

    status = await health_monitor.get_status()
    assert "uptime" in status
    assert "memory" in status

    with patch.object(buketov_api_service, "fetch_schedule", new=AsyncMock(return_value={"records": [{"id": 1}]})):
        hc = await buketov_api_service.health_check()
        assert hc is True


@pytest.mark.asyncio
async def test_smart_search_warmup_indexes_fields(mock_db):
    """Verify smart_search_service.warmup() precomputes normalized fields on groups and teachers."""
    from src.database.models.group import Group
    from src.services.smart_search import smart_search_service

    await Group(id=3002, name="ВТиПО-2401", language="ru", faculty=10, program=100).insert()
    smart_search_service.invalidate_caches()
    await smart_search_service.warmup()
    groups = await smart_search_service.get_all_groups()
    teachers = await smart_search_service.get_all_teachers()
    assert len(groups) > 0
    assert "_norm_name" in groups[0]
    assert len(teachers) > 0
    assert "_norm_full" in teachers[0]



