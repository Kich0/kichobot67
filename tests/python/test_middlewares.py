"""Unit tests for Phase 5 Middlewares: MessageGateMiddleware and UserActionLoggingMiddleware."""

import asyncio
from datetime import datetime, timezone
import time
from unittest.mock import AsyncMock, MagicMock
import pytest
from aiogram.types import Animation, Audio, CallbackQuery, Chat, Document, Message, PhotoSize, Sticker, User, Voice

from src.bot.middlewares.message_gate import MessageGateMiddleware
from src.bot.middlewares.user_action_logger import (
    UserActionLoggingMiddleware,
    describe_callback_query,
    format_capped_text,
    get_media_category,
    register_department_name,
    register_faculty_name,
    register_group_name,
    register_program_name,
    register_teacher_name,
    user_action_logging_middleware,
)
from src.database.models.black_list import BlackList
from src.database.models.user_action import UserAction


@pytest.fixture
def dummy_user():
    return User(id=12345, is_bot=False, first_name="Тест", username="testuser")


@pytest.fixture
def dummy_chat():
    return Chat(id=12345, type="private")


def make_message(dummy_user, dummy_chat, text="Привет", photo=None):
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=dummy_chat,
        from_user=dummy_user,
        text=text,
        photo=photo,
    )
    mock_answer = AsyncMock()
    object.__setattr__(msg, "answer", mock_answer)
    return msg


def make_callback(dummy_user, dummy_chat, data="nothing"):
    msg = make_message(dummy_user, dummy_chat)
    call = CallbackQuery(
        id="call_1",
        from_user=dummy_user,
        chat_instance="1",
        message=msg,
        data=data,
    )
    mock_answer = AsyncMock()
    object.__setattr__(call, "answer", mock_answer)
    return call


@pytest.mark.asyncio
async def test_message_gate_rate_limiting(dummy_user, dummy_chat):
    gate = MessageGateMiddleware(throttle_interval=0.5)
    handler = AsyncMock(return_value="OK")

    msg1 = make_message(dummy_user, dummy_chat, "Первое")
    res1 = await gate(handler, msg1, {})
    assert res1 == "OK"
    assert handler.call_count == 1

    # Second call immediately (< 500ms) should be dropped
    msg2 = make_message(dummy_user, dummy_chat, "Второе")
    res2 = await gate(handler, msg2, {})
    assert res2 is None
    assert handler.call_count == 1


@pytest.mark.asyncio
async def test_message_gate_spam_ban(dummy_user, dummy_chat):
    gate = MessageGateMiddleware(
        throttle_interval=0.0,
        spam_window=10.0,
        spam_max_requests=8,
        ban_duration=60.0,
    )
    handler = AsyncMock(return_value="OK")

    # Send 8 valid requests
    for i in range(8):
        msg = make_message(dummy_user, dummy_chat, f"Сообщение {i}")
        res = await gate(handler, msg, {})
        assert res == "OK"

    assert handler.call_count == 8
    assert not gate.is_user_banned(dummy_user.id)

    # 9th request triggers temporary spam ban
    msg9 = make_message(dummy_user, dummy_chat, "Спам 9")
    res9 = await gate(handler, msg9, {})
    assert res9 is None
    assert gate.is_user_banned(dummy_user.id)
    assert msg9.answer.call_count == 1
    assert "Блокировка на 60 секунд" in msg9.answer.call_args[0][0]

    # Subsequent requests while banned are rejected
    msg10 = make_message(dummy_user, dummy_chat, "Попытка 10")
    res10 = await gate(handler, msg10, {})
    assert res10 is None


@pytest.mark.asyncio
async def test_message_gate_blacklist_ban(mock_db, dummy_user, dummy_chat):
    gate = MessageGateMiddleware(throttle_interval=0.0)
    handler = AsyncMock(return_value="OK")

    # Insert admin ban into BlackList
    ban_doc = BlackList(
        userId=dummy_user.id,
        type="ban",
        reason="Нарушение правил",
        username=dummy_user.username,
    )
    await ban_doc.insert()

    msg = make_message(dummy_user, dummy_chat, "Команда")
    res = await gate(handler, msg, {})
    assert res is None
    assert msg.answer.call_count == 1
    assert "заблокирован администратором" in msg.answer.call_args[0][0]
    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_message_gate_rejects_non_text_in_private():
    user = User(id=77701, is_bot=False, first_name="Тест", username="user77701")
    chat = Chat(id=77701, type="private")
    gate = MessageGateMiddleware(throttle_interval=0.0)
    handler = AsyncMock(return_value="OK")

    # Message with photo and no text
    photo = [PhotoSize(file_id="p1", file_unique_id="pu1", width=100, height=100)]
    msg = make_message(user, chat, text=None, photo=photo)

    res = await gate(handler, msg, {})
    assert res is None
    assert msg.answer.call_count == 1
    assert "Бот принимает только текст" in msg.answer.call_args[0][0]
    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_message_gate_max_length_rejection():
    user = User(id=77702, is_bot=False, first_name="Тест", username="user77702")
    chat = Chat(id=77702, type="private")
    gate = MessageGateMiddleware(throttle_interval=0.0, max_text_length=250)
    handler = AsyncMock(return_value="OK")

    long_text = "А" * 251
    msg = make_message(user, chat, text=long_text)

    res = await gate(handler, msg, {})
    assert res is None
    assert msg.answer.call_count == 1
    assert "Слишком длинное сообщение" in msg.answer.call_args[0][0]
    assert handler.call_count == 0


def test_format_capped_text():
    assert format_capped_text(None) == ""
    assert format_capped_text("") == ""
    assert format_capped_text("Короткий текст") == "Короткий текст"

    # Caps words exceeding 50 (with short 1-char words so total len < 250)
    words_55 = " ".join([f"w{i}" for i in range(55)])
    capped = format_capped_text(words_55)
    assert len(capped.split()) == 50

    # Caps characters exceeding 250
    chars_300 = "x" * 300
    assert len(format_capped_text(chars_300)) == 250


def test_get_media_category(dummy_user, dummy_chat):
    photo_msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=dummy_chat,
        from_user=dummy_user,
        photo=[PhotoSize(file_id="1", file_unique_id="u1", width=10, height=10)],
    )
    act, label = get_media_category(photo_msg)
    assert act == "media_photo"
    assert "фото" in label


@pytest.mark.asyncio
async def test_user_action_logger_middleware(mock_db, dummy_user, dummy_chat):
    logger_mw = UserActionLoggingMiddleware()
    handler = AsyncMock(return_value="OK")

    msg = make_message(dummy_user, dummy_chat, text="/start")
    res = await logger_mw(handler, msg, {})
    assert res == "OK"

    # Wait briefly for async create_task insert
    await asyncio.sleep(0.1)

    records = await UserAction.find({"userId": dummy_user.id}).to_list()
    assert len(records) == 1
    record = records[0]
    assert record.user_id == dummy_user.id
    assert record.action == "command"
    assert record.is_first is True
    assert "🆕 [Первое сообщение пользователя]" in record.text

    # Second action from same user is not marked as first message
    msg2 = make_message(dummy_user, dummy_chat, text="ИС-21-1")
    await logger_mw(handler, msg2, {})
    await asyncio.sleep(0.1)

    records2 = await UserAction.find({"userId": dummy_user.id}).to_list()
    assert len(records2) == 2
    rec2 = [r for r in records2 if r.text == "ИС-21-1"][0]
    assert rec2.is_first is False


def test_describe_callback_query_all_types():
    register_faculty_name(10, "Физико-технический")
    register_program_name(61, "Радиотехника")
    register_group_name(6474, "РЭТ-21")
    register_department_name(5, "Кафедра физики")
    register_teacher_name(101, "Иванов И.И.")

    # start, delete, nothing
    act, text, _, _ = describe_callback_query("start")
    assert act == "start_menu"
    assert "главный экран" in text

    act, text, _, _ = describe_callback_query("delete")
    assert act == "delete_message"
    assert "Удалил сообщение" in text

    act, text, _, _ = describe_callback_query("nothing")
    assert act == "nothing"

    # faculty
    act, text, fid, fname = describe_callback_query("faculty|0")
    assert act == "faculty_menu"
    assert "стр. 1" in text

    # program
    act, text, pid, pname = describe_callback_query("program|10|0")
    assert act == "program_menu"
    assert "Физико-технический" in text

    # group
    act, text, gid, gname = describe_callback_query("group|10|61|0")
    assert act == "group_menu"
    assert "Радиотехника" in text

    # schedule & refresh_schedule
    act, text, gid, gname = describe_callback_query("schedule|рус|6474|0")
    assert act == "view_group"
    assert "РЭТ-21" in text
    assert "Понедельник" in text

    act, text, gid, gname = describe_callback_query("refresh_schedule|рус|6474|1")
    assert act == "refresh_group"
    assert "РЭТ-21" in text
    assert "Вторник" in text

    # department
    act, text, _, _ = describe_callback_query("department|1")
    assert act == "department_menu"
    assert "стр. 2" in text

    # teacher
    act, text, did, dname = describe_callback_query("teacher|5|0")
    assert act == "teacher_menu"
    assert "Кафедра физики" in text

    # TeacherSchedule & refreshTeacherSchedule
    act, text, tid, tname = describe_callback_query("TeacherSchedule|101|2")
    assert act == "view_teacher"
    assert "Иванов И.И." in text
    assert "Среда" in text

    act, text, tid, tname = describe_callback_query("refreshTeacherSchedule|101|3")
    assert act == "refresh_teacher"
    assert "Иванов И.И." in text
    assert "Четверг" in text

    # teacherImg, refreshteacherImg, teacherText, profile
    act, text, tid, tname = describe_callback_query("teacherImg|101")
    assert act == "view_teacher_image"
    assert "Иванов И.И." in text

    act, text, tid, tname = describe_callback_query("refreshteacherImg|101")
    assert act == "refresh_teacher_image"
    assert "Иванов И.И." in text

    act, text, tid, tname = describe_callback_query("teacherText|101")
    assert act == "view_teacher_text"
    assert "Иванов И.И." in text

    act, text, tid, tname = describe_callback_query("profile|101")
    assert act == "view_profile"
    assert "Иванов И.И." in text

    # chooseScheduleLanguage
    act, text, gid, gname = describe_callback_query("chooseScheduleLanguage|рус|6474|0")
    assert act == "choose_schedule_language"
    assert "РЭТ-21" in text

    # searchGroup & searchTeacher
    act, text, _, _ = describe_callback_query("searchGroup|ФИЗ|0")
    assert act == "search_group_page"
    assert "ФИЗ" in text

    act, text, _, _ = describe_callback_query("searchTeacher|Ахметов|1")
    assert act == "search_teacher_page"
    assert "Ахметов" in text

    # languageIs
    act, text, _, _ = describe_callback_query("languageIskz")
    assert act == "change_language"
    assert "Қазақша" in text

    act, text, _, _ = describe_callback_query("languageIsru")
    assert act == "change_language"
    assert "Русский" in text

    # language_select
    act, text, _, _ = describe_callback_query("language_select")
    assert act == "language_menu"
    assert "выбора языка" in text

    # 2-part schedule and refreshschedule format
    act, text, gid, gname = describe_callback_query("schedule|6474|0")
    assert act == "view_group"
    assert gid == 6474
    assert "РЭТ-21" in text
    assert "Понедельник" in text

    act, text, gid, gname = describe_callback_query("refreshschedule|6474|1")
    assert act == "refresh_group"
    assert gid == 6474
    assert "РЭТ-21" in text
    assert "Вторник" in text

    act, text, gid, gname = describe_callback_query("chooseScheduleLanguage|6474|0")
    assert act == "choose_schedule_language"
    assert gid == 6474
    assert "РЭТ-21" in text

    # String day name in callback
    act, text, gid, gname = describe_callback_query("schedule|рус|6474|среда")
    assert act == "view_group"
    assert "Среда" in text

    # refreshchooseScheduleLanguage
    act, text, gid, gname = describe_callback_query("refreshchooseScheduleLanguage|рус|6474|2")
    assert act == "refresh_group"
    assert gid == 6474
    assert "РЭТ-21" in text
    assert "Среда" in text

    # Case-insensitive callbacks
    act, text, tid, tname = describe_callback_query("teacherschedule|101|1")
    assert act == "view_teacher"
    assert tid == 101
    assert "Вторник" in text

    act, text, tid, tname = describe_callback_query("refreshteacherschedule|101|2")
    assert act == "refresh_teacher"
    assert tid == 101
    assert "Среда" in text

    act, text, tid, tname = describe_callback_query("refreshTeacherImg|101")
    assert act == "refresh_teacher_image"
    assert tid == 101


@pytest.mark.asyncio
async def test_user_action_logging_menu_button_prefixes(dummy_user, dummy_chat):
    from unittest.mock import patch
    from src.bot.middlewares.user_action_logger import user_action_logging_middleware

    handler = AsyncMock(return_value="OK")
    with patch.object(user_action_logging_middleware, "log_action", AsyncMock()) as mock_log:
        msg = make_message(dummy_user, dummy_chat, text="❓ Помощь")
        await user_action_logging_middleware(handler, msg, {})
        mock_log.assert_awaited_once()
        args, _ = mock_log.call_args
        assert args[2] == "menu_button"
        assert args[3] == "❓ Помощь"
async def test_message_gate_media_logging(dummy_user, dummy_chat):
    gate = MessageGateMiddleware(throttle_interval=0.0)
    handler = AsyncMock(return_value="OK")

    from unittest.mock import patch
    with patch.object(user_action_logging_middleware, "log_action", MagicMock()) as mock_log:
        photo = [PhotoSize(file_id="p1", file_unique_id="pu1", width=100, height=100)]
        msg = make_message(dummy_user, dummy_chat, text=None, photo=photo)
        object.__setattr__(msg, "caption", "Мое фото")

        res = await gate(handler, msg, {})
        assert res is None
        assert msg.answer.call_count == 1
        assert "Бот принимает только текст" in msg.answer.call_args[0][0]
        assert handler.call_count == 0
        mock_log.assert_called_once()
        args, _ = mock_log.call_args
        assert args[0] == dummy_user.id
        assert args[2] == "media_photo"
        assert '[Медиа: фото] "Мое фото"' in args[3]


@pytest.mark.asyncio
async def test_message_gate_long_text_logging(dummy_user, dummy_chat):
    gate = MessageGateMiddleware(throttle_interval=0.0, max_text_length=250)
    handler = AsyncMock(return_value="OK")

    from unittest.mock import patch
    with patch.object(user_action_logging_middleware, "log_action", MagicMock()) as mock_log:
        long_text = "Текст сообщения " * 20  # > 250 chars
        msg = make_message(dummy_user, dummy_chat, text=long_text)

        res = await gate(handler, msg, {})
        assert res is None
        assert msg.answer.call_count == 1
        assert "Слишком длинное сообщение" in msg.answer.call_args[0][0]
        assert handler.call_count == 0
        mock_log.assert_called_once()
        args, _ = mock_log.call_args
        assert args[0] == dummy_user.id
        assert args[2] == "long_text_rejected"
        assert "[Слишком длинное сообщение" in args[3]


@pytest.mark.asyncio
async def test_message_gate_spam_ban_logging(dummy_user, dummy_chat):
    gate = MessageGateMiddleware(
        throttle_interval=0.0,
        spam_window=10.0,
        spam_max_requests=8,
        ban_duration=60.0,
    )
    handler = AsyncMock(return_value="OK")

    from unittest.mock import patch
    with patch.object(user_action_logging_middleware, "log_action", MagicMock()) as mock_log:
        for i in range(8):
            msg = make_message(dummy_user, dummy_chat, f"Сообщение {i}")
            await gate(handler, msg, {})

        msg_ban = make_message(dummy_user, dummy_chat, "Спам блокировка")
        res = await gate(handler, msg_ban, {})
        assert res is None
        assert gate.is_user_banned(dummy_user.id)
        mock_log.assert_called_once()
        args, _ = mock_log.call_args
        assert args[0] == dummy_user.id
        assert args[2] == "spam_blocked"
        assert "Временный бан на 60 сек" in args[3]

