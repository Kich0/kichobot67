"""Unit tests for WarmupLog model, TTL index, SmartWarmupEngine logging, and schedule source badges."""

import asyncio
from datetime import datetime, timezone
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pymongo import ASCENDING, DESCENDING

from src.bot.middlewares.user_action_logger import (
    UserActionLoggingMiddleware,
    describe_callback_query,
    format_capped_text,
    register_group_name,
    register_teacher_name,
)
from src.cron.cache_warmup import SmartWarmupEngine
from src.database.models import ALL_DOCUMENT_MODELS
from src.database.models.user_action import UserAction
from src.database.models.warmup_log import WarmupLog


def test_warmup_log_model_in_all_models():
    """Verifies WarmupLog is registered in ALL_DOCUMENT_MODELS for Beanie."""
    assert WarmupLog in ALL_DOCUMENT_MODELS


def test_warmup_log_settings_and_indexes():
    """Verifies collection name and indexes including 10-day TTL index."""
    assert WarmupLog.Settings.name == "warmuplogs"

    indexes = WarmupLog.Settings.indexes
    assert len(indexes) >= 3

    # Check 10-day TTL index: expireAfterSeconds = 10 * 24 * 60 * 60 = 864000
    ttl_idx = next(
        (idx for idx in indexes if getattr(idx, "document", {}).get("expireAfterSeconds") == 10 * 24 * 60 * 60),
        None,
    )
    assert ttl_idx is not None
    assert dict(ttl_idx.document.get("key")) == {"createdAt": ASCENDING}

    # Check name index
    name_idx = next(
        (idx for idx in indexes if dict(getattr(idx, "document", {}).get("key", {})) == {"name": ASCENDING}),
        None,
    )
    assert name_idx is not None

    # Check compound type + createdAt index
    compound_idx = next(
        (idx for idx in indexes if dict(getattr(idx, "document", {}).get("key", {})) == {"type": ASCENDING, "createdAt": DESCENDING}),
        None,
    )
    assert compound_idx is not None


@pytest.mark.asyncio
async def test_warmup_log_instantiation_defaults_and_aliases(mock_db):
    """Verifies field initialization with python attributes and MongoDB aliases."""
    # Instantiation via python attributes
    log1 = WarmupLog(
        name="ИС-23-1",
        target_type="group",
        badge="🟢",
        source="api",
        duration_ms=45,
        status=True,
    )
    assert log1.name == "ИС-23-1"
    assert log1.target_type == "group"
    assert log1.badge == "🟢"
    assert log1.source == "api"
    assert log1.duration_ms == 45
    assert log1.status is True
    assert log1.error_msg is None
    assert log1.created_at is not None

    # Instantiation via MongoDB aliases (type, src, ms, ok, err, createdAt)
    now_dt = datetime.now(timezone.utc)
    log2 = WarmupLog(
        name="Иванов И.И.",
        type="teacher",
        badge="🔵",
        src="db",
        ms=120,
        ok=True,
        err=None,
        createdAt=now_dt,
    )
    assert log2.name == "Иванов И.И."
    assert log2.target_type == "teacher"
    assert log2.badge == "🔵"
    assert log2.source == "db"
    assert log2.duration_ms == 120
    assert log2.status is True
    assert log2.created_at == now_dt

    # Coercion and defaults
    log3 = WarmupLog(
        name="ПИ-22-1",
        type="group",
        src="cache",
        badge="🟣",
    )
    assert log3.duration_ms == 0
    assert log3.status is True
    assert log3.error_msg is None


@pytest.mark.asyncio
async def test_warmup_log_crud_operations(mock_db):
    """Verifies inserting and querying WarmupLog documents in database."""
    entry1 = WarmupLog(
        name="ИС-23-1",
        target_type="group",
        badge="🟢",
        source="api",
        duration_ms=50,
        status=True,
    )
    await entry1.insert()

    entry2 = WarmupLog(
        name="РЭТ-21",
        target_type="group",
        badge="🟣",
        source="cache",
        duration_ms=2,
        status=True,
    )
    await entry2.insert()

    entry3 = WarmupLog(
        name="Ахметов А.А.",
        target_type="teacher",
        badge="🔴",
        source="error",
        duration_ms=4500,
        status=False,
        error_msg="Connection timed out",
    )
    await entry3.insert()

    # Query all
    all_logs = await WarmupLog.find_all().to_list()
    assert len(all_logs) == 3

    # Query by target_type
    group_logs = await WarmupLog.find(WarmupLog.target_type == "group").to_list()
    assert len(group_logs) == 2

    # Query by badge
    err_logs = await WarmupLog.find(WarmupLog.badge == "🔴").to_list()
    assert len(err_logs) == 1
    assert err_logs[0].name == "Ахметов А.А."
    assert err_logs[0].error_msg == "Connection timed out"

    # Query raw collection
    raw_doc = await mock_db["warmuplogs"].find_one({"name": "ИС-23-1"})
    assert raw_doc is not None
    assert raw_doc["type"] == "group"
    assert raw_doc["src"] == "api"
    assert raw_doc["ms"] == 50
    assert raw_doc["ok"] is True


@pytest.mark.asyncio
async def test_smart_warmup_engine_log_helper(mock_db):
    """Verifies SmartWarmupEngine._log_warmup_result background persistence."""
    engine = SmartWarmupEngine()
    engine._log_warmup_result(
        name="ВТПО-20-1",
        target_type="group",
        badge="🟢",
        source="api",
        duration_ms=35,
        status=True,
    )

    # Allow fire-and-forget task to complete
    await asyncio.sleep(0.05)

    doc = await WarmupLog.find_one(WarmupLog.name == "ВТПО-20-1")
    assert doc is not None
    assert doc.badge == "🟢"
    assert doc.source == "api"
    assert doc.duration_ms == 35


@pytest.mark.asyncio
async def test_warm_group_logging_api_and_cache(mock_db):
    """Verifies that _warm_group logs 🟢 on fresh API call and 🟣 on cached call."""
    engine = SmartWarmupEngine()
    group_name = "ТЕСТ-ГР-1"
    group_id = 99901

    from src.bot.handlers.callbacks_router import student_schedule_cache
    for k in (f"{group_id}_ru", f"{group_id}_kz", f"{group_id}_рус", f"{group_id}_каз"):
        student_schedule_cache.pop(k, None)

    fake_api_data = {
        "records": [{"dayOfWeek": 1, "day": "Понедельник", "time": "08:30-09:20", "subject": "Математика", "room": "101"}],
        "_fromDb": False,
        "_cacheTimestamp": time.time(),
    }

    with patch("src.services.buketov_api.buketov_api_service.get_group_schedule", new=AsyncMock(return_value=fake_api_data)):
        await engine._warm_group(group_id, group_name)

    await asyncio.sleep(0.05)

    log1 = await WarmupLog.find_one(WarmupLog.name == group_name)
    assert log1 is not None
    assert log1.badge == "🟢"
    assert log1.source == "api"
    assert log1.status is True

    # Immediate second call within 3 minutes must hit RAM cache check and log 🟣
    with patch("src.services.buketov_api.buketov_api_service.get_group_schedule", new=AsyncMock()) as mock_get:
        await engine._warm_group(group_id, group_name)
        assert mock_get.call_count == 0

    await asyncio.sleep(0.05)
    # MongoDB Atlas saves only fresh API, DB fallback, or error logs (saving 38k idle TLS writes/day)
    db_logs = await WarmupLog.find(WarmupLog.name == group_name).to_list()
    assert len(db_logs) == 1
    assert db_logs[0].badge == "🟢"
    assert db_logs[0].source == "api"

    # In-memory ring buffer captures all ticks including cache hits (🟣)
    recent = engine.get_recent_logs()
    assert len(recent) >= 2
    assert recent[-1]["badge"] == "🟣"
    assert recent[-1]["source"] == "cache"


@pytest.mark.asyncio
async def test_warm_group_logging_db_fallback_and_error(mock_db):
    """Verifies that _warm_group logs 🔵 on DB fallback and 🔴 on network exception."""
    engine = SmartWarmupEngine()
    group_name = "ТЕСТ-ГР-2"
    group_id = 99902

    from src.bot.handlers.callbacks_router import student_schedule_cache
    for k in (f"{group_id}_ru", f"{group_id}_kz"):
        student_schedule_cache.pop(k, None)

    # 1. DB fallback test (circuit breaker open or network down)
    fake_db_data = {
        "records": [{"dayOfWeek": 1, "day": "Понедельник", "time": "08:30-09:20", "subject": "Физика", "room": "202"}],
        "_fromDb": True,
        "_cacheTimestamp": time.time(),
    }
    with patch("src.services.buketov_api.buketov_api_service.get_group_schedule", new=AsyncMock(return_value=fake_db_data)):
        await engine._warm_group(group_id, group_name)

    await asyncio.sleep(0.05)
    log_db = await WarmupLog.find_one(WarmupLog.name == group_name)
    assert log_db is not None
    assert log_db.badge == "🔵"
    assert log_db.source == "db"

    # 2. Exception test
    err_group_name = "ТЕСТ-ГР-ERR"
    err_group_id = 99903
    with patch("src.services.buketov_api.buketov_api_service.get_group_schedule", new=AsyncMock(side_effect=RuntimeError("API Network Crash"))):
        await engine._warm_group(err_group_id, err_group_name)

    await asyncio.sleep(0.05)
    log_err = await WarmupLog.find_one(WarmupLog.name == err_group_name)
    assert log_err is not None
    assert log_err.badge == "🔴"
    assert log_err.source == "error"
    assert log_err.status is False
    assert "API Network Crash" in (log_err.error_msg or "")


@pytest.mark.asyncio
async def test_warm_teacher_logging_api_cache_and_error(mock_db):
    """Verifies _warm_teacher logging with 🟢, 🟣, and 🔴."""
    engine = SmartWarmupEngine()
    teacher_name = "Серіков Б.С."
    teacher_id = 88801

    from src.bot.handlers.callbacks_router import teacher_text_cache
    for k in (f"{teacher_id}_ru", f"{teacher_id}_kz", f"{teacher_name}_ru", f"{teacher_name}_kz"):
        teacher_text_cache.pop(k, None)

    fake_teacher_api = {
        "records": [{"dayOfWeek": 1, "day": "Понедельник", "time": "10:00-10:50", "subject": "Информатика", "room": "303"}],
        "_fromDb": False,
        "_cacheTimestamp": time.time(),
    }

    with patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", new=AsyncMock(return_value=fake_teacher_api)):
        await engine._warm_teacher(teacher_id, teacher_name)

    await asyncio.sleep(0.05)
    t_log = await WarmupLog.find_one(WarmupLog.name == teacher_name)
    assert t_log is not None
    assert t_log.badge == "🟢"
    assert t_log.source == "api"

    # Repeat call hits cache (< 120s) -> 🟣
    with patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", new=AsyncMock()) as mock_t_get:
        await engine._warm_teacher(teacher_id, teacher_name)
        assert mock_t_get.call_count == 0

    await asyncio.sleep(0.05)
    # MongoDB Atlas saves only fresh API, DB fallback, or error logs (saving 38k idle TLS writes/day)
    all_t_logs = await WarmupLog.find(WarmupLog.name == teacher_name).sort(-WarmupLog.created_at).to_list()
    assert len(all_t_logs) == 1
    assert all_t_logs[0].badge == "🟢"
    assert all_t_logs[0].source == "api"

    # In-memory ring buffer captures all ticks including cache hits (🟣)
    recent_t = engine.get_recent_logs()
    assert len(recent_t) >= 2
    assert recent_t[-1]["badge"] == "🟣"
    assert recent_t[-1]["source"] == "cache"

    # Teacher error path
    err_t_name = "Калиев К.К."
    err_t_id = 88802
    with patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", new=AsyncMock(side_effect=TimeoutError("HTTP Timeout"))):
        await engine._warm_teacher(err_t_id, err_t_name)

    await asyncio.sleep(0.05)
    t_err_log = await WarmupLog.find_one(WarmupLog.name == err_t_name)
    assert t_err_log is not None
    assert t_err_log.badge == "🔴"
    assert t_err_log.source == "error"
    assert t_err_log.status is False


def test_describe_callback_query_with_schedule_badge():
    """Verifies describe_callback_query enriches description with [🟢], [🟣], [🔵]."""
    register_group_name(1234, "ИС-23-1")
    register_teacher_name(567, "Калиев К.К.")

    # 1. Schedule group with 🟢
    act, desc, gid, gname = describe_callback_query("schedule|рус|1234|1", schedule_badge="🟢")
    assert act == "view_group"
    assert desc.startswith("[🟢]")
    assert "ИС-23-1" in desc
    assert "Вторник" in desc

    # 2. Schedule group with 🟣 (cache)
    act, desc, gid, gname = describe_callback_query("refresh_schedule|рус|1234|2", schedule_badge="🟣")
    assert act == "refresh_group"
    assert desc.startswith("[🟣]")
    assert "Среда" in desc

    # 3. Schedule group with 🔵 (MongoDB fallback)
    act, desc, gid, gname = describe_callback_query("schedule|рус|1234|0", schedule_badge="🔵")
    assert act == "view_group"
    assert desc.startswith("[🔵]")

    # 4. Teacher schedule with badge
    act, desc, tid, tname = describe_callback_query("TeacherSchedule|567|3", schedule_badge="🟢")
    assert act == "view_teacher"
    assert desc.startswith("[🟢]")
    assert "Калиев К.К." in desc

    # 5. Teacher table image with badge
    act, desc, tid, tname = describe_callback_query("teacherImg|567", schedule_badge="🟣")
    assert act == "view_teacher_image"
    assert desc.startswith("[🟣]")
    assert "Калиев К.К." in desc

    # 6. If raw text already starts with badge, preserves cleanly
    act, desc, _, _ = describe_callback_query("[🟢] Открыл расписание группы ИС-23-1 (Вторник)")
    assert desc == "[🟢] Открыл расписание группы ИС-23-1 (Вторник)"

    # 7. format_capped_text preserves badge
    capped = format_capped_text("[🟢] Открыл расписание группы ИС-23-1 (Вторник)")
    assert capped == "[🟢] Открыл расписание группы ИС-23-1 (Вторник)"


@pytest.mark.asyncio
async def test_middleware_captures_schedule_badge(mock_db):
    """Verifies UserActionLoggingMiddleware receives data['schedule_badge'] from handler and logs it."""
    middleware = UserActionLoggingMiddleware()
    user_id = 99999

    from aiogram.types import CallbackQuery, User, Message, Chat
    user = User(id=user_id, is_bot=False, first_name="Тестер")
    chat = Chat(id=user_id, type="private")
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=chat, from_user=user, text="test")

    call = CallbackQuery(id="c1", from_user=user, chat_instance="1", message=msg, data="schedule|1234|0")

    async def mock_handler(event, data):
        # Handler marks the schedule source badge
        data["schedule_badge"] = "🟢"
        return "OK"

    await middleware(mock_handler, call, {})
    await asyncio.sleep(0.05)

    actions = await UserAction.find(UserAction.user_id == user_id).to_list()
    assert len(actions) == 1
    assert "[🟢]" in actions[0].text


@pytest.mark.asyncio
async def test_handle_teacher_table_image_concurrent_refresh_badge():
    """Verifies handle_teacher_table_image sets 🟣 badge when another request already refreshed within 5s."""
    from src.bot.handlers.callbacks_router import handle_teacher_table_image, teacher_table_cache

    teacher_id = 998877
    now = time.time()
    # Cache is 1205s old so needs_fetch is True
    entry = {"timestamp": now - 1205, "is_from_db": False, "data": []}
    teacher_table_cache[str(teacher_id)] = entry

    call = MagicMock()
    call.data = f"refreshteacherImg|{teacher_id}|1"
    call.message = None

    # When call.answer is called on line 2067, simulate background completion updating entry timestamp
    async def mock_answer(*args, **kwargs):
        entry["timestamp"] = time.time()
        return None

    call.answer = mock_answer

    data = {}
    await handle_teacher_table_image(call, data=data)
    assert data.get("schedule_badge") == "🟣"


@pytest.mark.asyncio
async def test_real_handlers_set_schedule_badge(mock_db):
    """Verifies handle_student_schedule_view and handle_teacher_text_schedule set schedule_badge on fresh and cached calls."""
    from src.bot.handlers.callbacks_router import (
        handle_student_schedule_view,
        handle_teacher_text_schedule,
        student_schedule_cache,
        teacher_text_cache,
    )

    # 1. Student schedule - fresh API call
    group_id = 887766
    for k in (f"{group_id}_ru", f"{group_id}_kz"):
        student_schedule_cache.pop(k, None)

    from src.database.models.group import Group
    await Group(id=group_id, name="ИС-23-1").insert()

    fake_sched = {
        "records": [{"dayOfWeek": 1, "day": "Понедельник", "time": "08:30-09:20", "subject": "Математика", "room": "101"}],
        "_fromDb": False,
        "_cacheTimestamp": time.time(),
    }

    call_stud = MagicMock()
    call_stud.data = f"schedule|ru|{group_id}|1"
    call_stud.from_user.id = 112233
    call_stud.message = MagicMock()
    call_stud.message.edit_text = AsyncMock()
    call_stud.answer = AsyncMock()

    data_stud = {}
    with patch("src.services.buketov_api.buketov_api_service.get_group_schedule", new=AsyncMock(return_value=fake_sched)):
        await handle_student_schedule_view(call_stud, data=data_stud)

    assert data_stud.get("schedule_badge") == "🟢"

    # Repeat call -> from cache (🟣)
    data_stud_cache = {}
    await handle_student_schedule_view(call_stud, data=data_stud_cache)
    assert data_stud_cache.get("schedule_badge") == "🟣"

    # 2. Teacher text schedule - fresh API call
    teacher_id = 776655
    teacher_name = "Аманов А.А."
    for k in (f"{teacher_id}_ru", f"{teacher_name}_ru"):
        teacher_text_cache.pop(k, None)

    call_tch = MagicMock()
    call_tch.data = f"TeacherSchedule|{teacher_id}|1"
    call_tch.from_user.id = 112234
    call_tch.message = MagicMock()
    call_tch.message.photo = None
    call_tch.message.edit_text = AsyncMock()
    call_tch.message.answer = AsyncMock()
    call_tch.answer = AsyncMock()

    data_tch = {}
    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", new=AsyncMock(return_value=(teacher_id, teacher_name, 1, teacher_name))):
        with patch("src.services.buketov_api.buketov_api_service.get_teacher_schedule", new=AsyncMock(return_value=fake_sched)):
            await handle_teacher_text_schedule(call_tch, data=data_tch)

    assert data_tch.get("schedule_badge") == "🟢"

    # Teacher repeat call -> from cache (🟣)
    data_tch_cache = {}
    with patch("src.bot.handlers.callbacks_router.resolve_teacher_identity", new=AsyncMock(return_value=(teacher_id, teacher_name, 1, teacher_name))):
        await handle_teacher_text_schedule(call_tch, data=data_tch_cache)

    assert data_tch_cache.get("schedule_badge") == "🟣"


@pytest.mark.asyncio
async def test_warmup_engine_unresolved_name_logging(mock_db):
    """Verifies that unresolved group and teacher names are logged as errors with 🔴 badge."""
    engine = SmartWarmupEngine()

    # 1. Group with unresolved name
    await engine._warm_group(group_id=999999, group_name="")
    await asyncio.sleep(0.05)

    err_group_log = await WarmupLog.find_one(WarmupLog.name == "Group 999999")
    assert err_group_log is not None
    assert err_group_log.badge == "🔴"
    assert err_group_log.source == "error"
    assert err_group_log.status is False
    assert "not resolved" in (err_group_log.error_msg or "")

    # 2. Teacher with unresolved name
    await engine._warm_teacher(teacher_id=999998, teacher_name="")
    await asyncio.sleep(0.05)

    err_tch_log = await WarmupLog.find_one(WarmupLog.name == "Teacher 999998")
    assert err_tch_log is not None
    assert err_tch_log.badge == "🔴"
    assert err_tch_log.source == "error"
    assert err_tch_log.status is False
    assert "not resolved" in (err_tch_log.error_msg or "")


@pytest.mark.asyncio
async def test_middleware_exception_safety_in_finally(mock_db):
    """Verifies middleware returns handler result even if logger raises an exception in finally block."""
    middleware = UserActionLoggingMiddleware()
    user_id = 99998

    from aiogram.types import CallbackQuery, User, Message, Chat
    user = User(id=user_id, is_bot=False, first_name="Тестер")
    chat = Chat(id=user_id, type="private")
    msg = Message(message_id=2, date=datetime.now(timezone.utc), chat=chat, from_user=user, text="test")
    call = CallbackQuery(id="c2", from_user=user, chat_instance="2", message=msg, data="schedule|1234|0")

    async def mock_handler(event, data):
        data["schedule_badge"] = "🟢"
        return "SUCCESS_RESULT"

    with patch.object(middleware, "log_action", side_effect=RuntimeError("Logging DB error")):
        result = await middleware(mock_handler, call, {})
        assert result == "SUCCESS_RESULT"


def test_export_script_warmup_formatting_offline():
    """Verifies openpyxl styling and row preparation in export script."""
    from scratch.export_database_to_drive import create_workbook_with_sheet

    cols = [
        {"header": "Дата и Время", "key": "time", "width": 22},
        {"header": "Тип", "key": "type", "width": 16},
        {"header": "Название", "key": "name", "width": 28},
        {"header": "Статус", "key": "status", "width": 15},
        {"header": "Источник", "key": "source", "width": 18},
        {"header": "Отклик (мс)", "key": "duration_ms", "width": 15},
        {"header": "Примечание", "key": "note", "width": 45},
    ]
    rows = [
        {
            "time": "18.10.2026, 12:00:00",
            "type": "Группа",
            "name": "ИС-23-1",
            "status": "✅ Успешно",
            "source": "🟢 API КарУ",
            "duration_ms": 45,
            "note": "Свежий запрос (API)",
            "_badge": "🟢",
            "_ok": True,
        },
        {
            "time": "18.10.2026, 12:01:00",
            "type": "Преподаватель",
            "name": "Калиев К.К.",
            "status": "❌ Ошибка",
            "source": "🔴 Ошибка сети",
            "duration_ms": 4500,
            "note": "Connection timed out",
            "_badge": "🔴",
            "_ok": False,
        },
    ]

    wb = create_workbook_with_sheet("Прогрев расписаний", cols, rows)
    ws = wb.active
    assert ws.title == "Прогрев расписаний"
    assert ws.cell(row=1, column=1).value == "Дата и Время"
    assert ws.cell(row=2, column=3).value == "ИС-23-1"
    assert ws.cell(row=3, column=3).value == "Калиев К.К."
