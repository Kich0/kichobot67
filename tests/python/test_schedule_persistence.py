"""Unit tests for background schedule persistence into MongoDB."""

import pytest

from src.bot.handlers.callbacks_router import (
    _bg_persist_student_schedule,
    _bg_persist_teacher_schedule,
)
from src.database.models.schedule import Schedule
from src.database.models.teacher_schedule import TeacherSchedule


@pytest.mark.asyncio
async def test_bg_persist_student_schedule(mock_db):
    """Verifies that fresh student schedule is saved and updated in Schedule collection."""
    group_id = 1234
    data_v1 = [{"day": "Понедельник", "subjects": [{"subject": "Math", "time": "08:30"}]}]

    await _bg_persist_student_schedule(group_id, data_v1)

    doc = await Schedule.find_one({"groupId": group_id})
    assert doc is not None
    assert doc.group_id == group_id
    assert len(doc.data) == 1
    assert doc.data[0].day == "Понедельник"
    initial_hash = doc.hash

    # Update with new data
    data_v2 = [
        {"day": "Понедельник", "subjects": [{"subject": "Math", "time": "08:30"}]},
        {"day": "Вторник", "subjects": [{"subject": "Physics", "time": "10:00"}]},
    ]
    await _bg_persist_student_schedule(group_id, data_v2)

    updated_doc = await Schedule.find_one({"groupId": group_id})
    assert updated_doc is not None
    assert len(updated_doc.data) == 2
    assert updated_doc.hash != initial_hash


@pytest.mark.asyncio
async def test_bg_persist_teacher_schedule(mock_db):
    """Verifies that fresh teacher schedule is saved and updated in TeacherSchedule collection."""
    teacher_id = 5678
    data_v1 = [{"day": "Среда", "groups": [{"group": "ИС-26-1", "time": "09:00"}]}]

    await _bg_persist_teacher_schedule(teacher_id, data_v1)

    doc = await TeacherSchedule.find_one({"teacherId": teacher_id})
    assert doc is not None
    assert doc.teacher_id == teacher_id
    assert len(doc.data) == 1
    assert doc.data[0].day == "Среда"

    # Update
    data_v2 = [{"day": "Среда", "groups": [{"group": "ИС-26-1", "time": "09:00"}, {"group": "ИС-25-1", "time": "10:30"}]}]
    await _bg_persist_teacher_schedule(teacher_id, data_v2)

    updated_doc = await TeacherSchedule.find_one({"teacherId": teacher_id})
    assert updated_doc is not None
    assert len(updated_doc.data[0].groups) == 2


@pytest.mark.asyncio
async def test_schedule_versioning_on_update(mock_db):
    """Verifies ScheduleVersion is created on genuine hash change and changed_at is set (Bug #13)."""
    from src.database.models.schedule import ScheduleVersion

    group_id = 9001
    data_v1 = [{"day": "Понедельник", "subjects": [{"subject": "Math", "time": "08:30"}]}]
    await _bg_persist_student_schedule(group_id, data_v1)

    doc = await Schedule.find_one({"groupId": group_id})
    assert doc is not None
    assert doc.changed_at is not None
    initial_hash = doc.hash

    # No version should exist yet
    versions = await ScheduleVersion.find({"groupId": group_id}).to_list()
    assert len(versions) == 0

    # Genuine update
    data_v2 = [
        {"day": "Понедельник", "subjects": [{"subject": "Math", "time": "08:30"}]},
        {"day": "Вторник", "subjects": [{"subject": "Physics", "time": "10:00"}]},
    ]
    await _bg_persist_student_schedule(group_id, data_v2)

    # Historical version must be archived
    versions = await ScheduleVersion.find({"groupId": group_id}).to_list()
    assert len(versions) == 1
    assert versions[0].hash == initial_hash
    assert len(versions[0].data) == 1

    updated_doc = await Schedule.find_one({"groupId": group_id})
    assert updated_doc.hash != initial_hash
    assert len(updated_doc.data) == 2


@pytest.mark.asyncio
async def test_teacher_schedule_versioning_on_update(mock_db):
    """Verifies TeacherScheduleVersion is created on genuine hash change (Bug #13)."""
    from src.database.models.teacher_schedule import TeacherScheduleVersion

    teacher_id = 9002
    data_v1 = [{"day": "Среда", "groups": [{"group": "ИС-26-1", "time": "09:00"}]}]
    await _bg_persist_teacher_schedule(teacher_id, data_v1)

    doc = await TeacherSchedule.find_one({"teacherId": teacher_id})
    assert doc is not None
    initial_hash = doc.hash

    # Genuine update
    data_v2 = [
        {"day": "Среда", "groups": [{"group": "ИС-26-1", "time": "09:00"}]},
        {"day": "Четверг", "groups": [{"group": "ИС-25-1", "time": "11:00"}]},
    ]
    await _bg_persist_teacher_schedule(teacher_id, data_v2)

    versions = await TeacherScheduleVersion.find({"teacherId": teacher_id}).to_list()
    assert len(versions) == 1
    assert versions[0].hash == initial_hash
    assert len(versions[0].data) == 1


@pytest.mark.asyncio
async def test_canonical_day_hashing_prevents_ru_kz_ping_pong(mock_db):
    """Verifies that persisting Kazakh day names normalizes to Russian canonical format (Bug #7)."""
    group_id = 9003
    kz_data = [{"day": "Дүйсенбі", "subjects": [{"subject": "Информатика", "time": "8.30-9.20"}]}]
    ru_data = [{"day": "Понедельник", "subjects": [{"subject": "Информатика", "time": "8.30-9.20"}]}]

    await _bg_persist_student_schedule(group_id, kz_data)
    doc_kz = await Schedule.find_one({"groupId": group_id})
    assert doc_kz is not None
    # Must be normalized to canonical Russian day
    assert doc_kz.data[0].day == "Понедельник"
    initial_hash = doc_kz.hash

    # Now persist Russian data: hash must be identical, preventing ping-pong updates
    await _bg_persist_student_schedule(group_id, ru_data)
    doc_ru = await Schedule.find_one({"groupId": group_id})
    assert doc_ru.hash == initial_hash


@pytest.mark.asyncio
async def test_get_or_fetch_student_schedule_concurrency_lock_and_dual_cache(mock_db):
    """Verifies per-entity lock deduplicates concurrent fetches and syncs RU/KZ cache (Bug #8)."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from src.bot.handlers.callbacks_router import (
        clear_schedule_caches,
        get_or_fetch_student_schedule,
        student_schedule_cache,
    )
    from src.database.models.group import Group

    clear_schedule_caches()
    group_id = 9004
    await Group(id=group_id, group_id=group_id, name="ИТ-24").insert()

    mock_raw = {
        "records": [
            {"dayIndex": 1, "time": "08:30-09:20", "subject": "Программирование", "slot": 1}
        ],
        "_fromDb": False,
    }

    call_count = 0

    async def fake_get_group_schedule(name, force_refresh=False):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)  # Simulate network latency
        return mock_raw

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", side_effect=fake_get_group_schedule):
        # Trigger two concurrent requests for the same group (simulating double-click 🔄)
        res_ru, res_kz = await asyncio.gather(
            get_or_fetch_student_schedule(group_id, "ru", is_refresh=True),
            get_or_fetch_student_schedule(group_id, "kz", is_refresh=True),
        )

        # Lock must ensure network fetch happened only ONCE
        assert call_count == 1
        assert res_ru is not None
        assert res_kz is not None
        assert res_ru["data"][0]["day"] == "Понедельник"
        assert res_kz["data"][0]["day"] == "Дүйсенбі"

        # Both language caches must be populated with identical timestamp
        assert f"{group_id}_ru" in student_schedule_cache
        assert f"{group_id}_kz" in student_schedule_cache
        assert student_schedule_cache[f"{group_id}_ru"]["timestamp"] == student_schedule_cache[f"{group_id}_kz"]["timestamp"]


@pytest.mark.asyncio
async def test_empty_fallback_short_ttl_bug6(mock_db):
    """Verifies empty API fallback is not cached for 30 minutes (Bug #6)."""
    import time
    from unittest.mock import AsyncMock, patch
    from src.bot.handlers.callbacks_router import (
        clear_schedule_caches,
        get_or_fetch_student_schedule,
        student_schedule_cache,
    )
    from src.database.models.group import Group

    clear_schedule_caches()
    group_id = 9005
    await Group(id=group_id, group_id=group_id, name="EMPTY-01").insert()

    # Buketov API returns empty fallback from DB (e.g. circuit breaker tripped and no DB entry)
    mock_empty_fallback = {
        "records": [],
        "_fromDb": True,
    }

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", AsyncMock(return_value=mock_empty_fallback)):
        now_ts = time.time()
        cached = await get_or_fetch_student_schedule(group_id, "ru", is_refresh=False)
        assert cached is not None
        # Effective TTL must be short (~20 seconds, so timestamp is backdated ~1780s)
        entry = student_schedule_cache[f"{group_id}_ru"]
        assert now_ts - entry["timestamp"] >= 1700.0  # Expiring soon!


@pytest.mark.asyncio
async def test_handle_teacher_table_image_uses_canonical_id(mock_db):
    """Verifies that handle_teacher_table_image uses resolved integer canonical_id (Bug #9)."""
    import time
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch
    from aiogram.types import CallbackQuery, Chat, Message, User
    from src.bot.handlers.callbacks_router import (
        clear_schedule_caches,
        handle_teacher_table_image,
        teacher_table_cache,
    )

    clear_schedule_caches()
    u = User(id=77701, is_bot=False, first_name="Test", username="testuser")
    c = Chat(id=77701, type="private")
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=c, from_user=u, text="tbl")
    object.__setattr__(msg, "answer", AsyncMock())
    object.__setattr__(msg, "delete", AsyncMock())
    object.__setattr__(msg, "edit_caption", AsyncMock())
    bot_mock = AsyncMock()
    object.__setattr__(msg, "_bot", bot_mock)

    call = CallbackQuery(id="c1", from_user=u, chat_instance="1", message=msg, data="teacherImg|Ivanov_Alias|0")
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", bot_mock)

    canonical_id = 8888
    teacher_name = "Иванов И. И."

    mock_raw = {
        "records": [{"teacher": teacher_name, "dayIndex": 1, "time": "8.30-9.20", "subject": "Math", "group": "ИС-21"}],
        "_fromDb": False,
    }

    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", AsyncMock(return_value=(canonical_id, teacher_name, 10, teacher_name))), \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=mock_raw)), \
         patch("src.services.table_image.table_image_service.render_table_image", AsyncMock(return_value=b"png_data")), \
         patch("src.services.table_image.table_image_service.cache_file_id") as mock_cache_fid, \
         patch("src.database.models.teacher_schedule.TeacherSchedule.find_one", AsyncMock(return_value=None)):

        await handle_teacher_table_image(call)

        # Table cache should contain the integer canonical string "8888"
        assert str(canonical_id) in teacher_table_cache

        # cache_file_id must receive canonical_id (8888), NOT "Ivanov_Alias"
        mock_cache_fid.assert_called_once()
        args, _ = mock_cache_fid.call_args
        assert args[0] == canonical_id


@pytest.mark.asyncio
async def test_teacher_table_image_concurrent_double_click_no_duplicate_render(mock_db):
    """Verifies that rapid double-clicking 🔄 on teacher table image does not render or send duplicate photos (Bug #8)."""
    import asyncio
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch
    from aiogram.types import CallbackQuery, Chat, Message, User
    from src.bot.handlers.callbacks_router import (
        clear_schedule_caches,
        handle_teacher_table_image,
        teacher_table_cache,
    )

    clear_schedule_caches()
    u = User(id=77702, is_bot=False, first_name="Test2", username="testuser2")
    c = Chat(id=77702, type="private")
    msg = Message(message_id=2, date=datetime.now(timezone.utc), chat=c, from_user=u, text="tbl")
    object.__setattr__(msg, "answer", AsyncMock())
    object.__setattr__(msg, "delete", AsyncMock())
    object.__setattr__(msg, "edit_caption", AsyncMock())
    bot_mock = AsyncMock()
    object.__setattr__(msg, "_bot", bot_mock)

    call1 = CallbackQuery(id="c2_1", from_user=u, chat_instance="1", message=msg, data="refreshteacherImg|5555|0")
    object.__setattr__(call1, "answer", AsyncMock())
    object.__setattr__(call1, "_bot", bot_mock)

    call2 = CallbackQuery(id="c2_2", from_user=u, chat_instance="1", message=msg, data="refreshteacherImg|5555|0")
    object.__setattr__(call2, "answer", AsyncMock())
    object.__setattr__(call2, "_bot", bot_mock)

    render_call_count = 0

    async def fake_render(*args, **kwargs):
        nonlocal render_call_count
        render_call_count += 1
        await asyncio.sleep(0.05)
        return b"png_data"

    mock_raw = {
        "records": [{"teacher": "Тестов Т. Т.", "dayIndex": 1, "time": "8.30-9.20", "subject": "Math", "group": "ИС-21"}],
        "_fromDb": False,
    }

    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", AsyncMock(return_value=(5555, "Тестов Т. Т.", 10, "Тестов Т. Т."))), \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=mock_raw)), \
         patch("src.services.table_image.table_image_service.render_table_image", side_effect=fake_render), \
         patch("src.database.models.teacher_schedule.TeacherSchedule.find_one", AsyncMock(return_value=None)):

        # Fire two concurrent refresh clicks
        await asyncio.gather(
            handle_teacher_table_image(call1),
            handle_teacher_table_image(call2),
        )

        # Image should be rendered exactly ONCE, not duplicated
        assert render_call_count == 1
        assert bot_mock.send_photo.call_count == 1


@pytest.mark.asyncio
async def test_teacher_table_image_ru_kz_language_isolation(mock_db):
    """Verifies that teacher table image dynamically localizes day names for RU and KZ users (Bug #7)."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch
    from aiogram.types import CallbackQuery, Chat, Message, User
    from src.bot.handlers.callbacks_router import (
        clear_schedule_caches,
        handle_teacher_table_image,
        teacher_table_cache,
    )
    from src.services.user_service import user_service

    clear_schedule_caches()
    u_ru = User(id=88801, is_bot=False, first_name="UserRU", username="user_ru")
    u_kz = User(id=88802, is_bot=False, first_name="UserKZ", username="user_kz")
    c = Chat(id=88801, type="private")
    msg = Message(message_id=3, date=datetime.now(timezone.utc), chat=c, from_user=u_ru, text="tbl")
    object.__setattr__(msg, "answer", AsyncMock())
    object.__setattr__(msg, "delete", AsyncMock())
    object.__setattr__(msg, "edit_caption", AsyncMock())
    bot_mock = AsyncMock()
    object.__setattr__(msg, "_bot", bot_mock)

    call_ru = CallbackQuery(id="c_ru", from_user=u_ru, chat_instance="1", message=msg, data="teacherImg|7777|0")
    object.__setattr__(call_ru, "answer", AsyncMock())
    object.__setattr__(call_ru, "_bot", bot_mock)

    call_kz = CallbackQuery(id="c_kz", from_user=u_kz, chat_instance="1", message=msg, data="teacherImg|7777|0")
    object.__setattr__(call_kz, "answer", AsyncMock())
    object.__setattr__(call_kz, "_bot", bot_mock)

    await user_service.set_user_language(88801, "ru")
    await user_service.set_user_language(88802, "kz")

    rendered_schedules = []

    async def fake_render(teacher, schedule_data, lang):
        rendered_schedules.append((lang, schedule_data))
        return b"png_bytes"

    mock_raw = {
        "records": [{"teacher": "Учитель У. У.", "dayIndex": 1, "time": "8.30-9.20", "subject": "Физика", "group": "Ф-21"}],
        "_fromDb": False,
    }

    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", AsyncMock(return_value=(7777, "Учитель У. У.", 10, "Учитель У. У."))), \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=mock_raw)), \
         patch("src.services.table_image.table_image_service.render_table_image", side_effect=fake_render), \
         patch("src.database.models.teacher_schedule.TeacherSchedule.find_one", AsyncMock(return_value=None)):

        # First request by Russian user
        await handle_teacher_table_image(call_ru)
        assert len(rendered_schedules) == 1
        lang_ru, sched_ru = rendered_schedules[0]
        assert lang_ru == "ru"
        assert sched_ru[0]["day"] == "Понедельник"

        # Second request by Kazakh user (hitting cache)
        await handle_teacher_table_image(call_kz)
        assert len(rendered_schedules) == 2
        lang_kz, sched_kz = rendered_schedules[1]
        assert lang_kz == "kz"
        # Must be dynamically localized to Kazakh day name, NOT stuck on Russian!
        assert sched_kz[0]["day"] == "Дүйсенбі"


@pytest.mark.asyncio
async def test_handle_teacher_text_schedule_null_guard():
    """Verifies that handle_teacher_text_schedule does not crash if cached is None."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch
    from aiogram.types import CallbackQuery, Chat, Message, User
    from src.bot.handlers.callbacks_router import (
        clear_schedule_caches,
        handle_teacher_text_schedule,
    )

    clear_schedule_caches()
    u = User(id=88803, is_bot=False, first_name="User3")
    c = Chat(id=88803, type="private")
    msg = Message(message_id=4, date=datetime.now(timezone.utc), chat=c, from_user=u, text="txt")
    object.__setattr__(msg, "answer", AsyncMock())
    object.__setattr__(msg, "edit_text", AsyncMock())
    object.__setattr__(msg, "delete", AsyncMock())
    bot_mock = AsyncMock()
    object.__setattr__(msg, "_bot", bot_mock)

    call = CallbackQuery(id="c_null", from_user=u, chat_instance="1", message=msg, data="teacherText|9999|0")
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", bot_mock)

    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", AsyncMock(return_value=(9999, "Преподаватель", 10, "Преподаватель"))), \
         patch("src.bot.handlers.callbacks_router.get_or_fetch_teacher_text_schedule", AsyncMock(return_value=None)):

        # Should not raise AttributeError: 'NoneType' object has no attribute 'get'
        await handle_teacher_text_schedule(call)
        msg.edit_text.assert_called_once()
        assert "временно недоступно" in msg.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_localize_schedule_days_sunday_and_deep_pydantic():
    """Verifies Sunday (day_idx=7) localization and deep dict extraction."""
    from src.bot.handlers.callbacks_router import _localize_schedule_days
    from src.database.models.schedule import ScheduleDay, SubjectItem

    day_obj = ScheduleDay(
        day="Воскресенье",
        subjects=[SubjectItem(time="10:00", subject="Math")]
    )

    localized_kz = _localize_schedule_days([day_obj], "kz")
    assert localized_kz[0]["day"] == "Жексенбі"
    assert localized_kz[0]["subjects"][0]["subject"] == "Math"

    localized_ru = _localize_schedule_days(localized_kz, "ru")
    assert localized_ru[0]["day"] == "Воскресенье"


