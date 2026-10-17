"""Unit tests for Phase 5 Middlewares: MessageGateMiddleware and UserActionLoggingMiddleware."""

import asyncio
from datetime import datetime, timezone
import time
from unittest.mock import AsyncMock, MagicMock, patch
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
    assert "Я не могу это прочитать" in msg.answer.call_args[0][0]
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
        assert "Я не могу это прочитать" in msg.answer.call_args[0][0]
        assert handler.call_count == 0
        mock_log.assert_called_once()
        args, _ = mock_log.call_args
        assert args[0] == dummy_user.id
        assert args[2] == "media_rejected"
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


@pytest.mark.asyncio
async def test_message_gate_strike_system(dummy_user, dummy_chat):
    """Verifies 1st strike (60s), repeat strike within 5 min (300s), and subsequent strikes (300s)."""
    gate = MessageGateMiddleware(
        throttle_interval=0.0,
        spam_window=3.0,
        spam_max_requests=5,
        first_ban_duration=60.0,
        repeat_window=300.0,
        repeat_ban_duration=300.0,
    )
    handler = AsyncMock(return_value="OK")

    current_simulated_time = 1000.0

    with patch("time.time", side_effect=lambda: current_simulated_time):
        # 1. Send 5 messages (under limit)
        for i in range(5):
            msg = make_message(dummy_user, dummy_chat, f"Msg {i}")
            res = await gate(handler, msg, {})
            assert res == "OK"

        assert handler.call_count == 5
        assert not gate.is_user_banned(dummy_user.id)

        # 2. 6th message triggers 1st strike (60s ban)
        msg_spam1 = make_message(dummy_user, dummy_chat, "Spam 6")
        res1 = await gate(handler, msg_spam1, {})
        assert res1 is None
        assert gate.is_user_banned(dummy_user.id)
        assert "Блокировка на 60 секунд" in msg_spam1.answer.call_args[0][0]

        # Messages while banned are dropped silently
        msg_during_ban = make_message(dummy_user, dummy_chat, "During ban")
        res_during = await gate(handler, msg_during_ban, {})
        assert res_during is None
        assert msg_during_ban.answer.call_count == 0

        # 3. Simulate 65 seconds later (ban expired, but within 300s repeat window)
        current_simulated_time = 1065.0
        assert not gate.is_user_banned(dummy_user.id)

        # 4. Spam again (5 msgs pass, 6th triggers repeat strike -> 300s ban)
        for i in range(5):
            msg = make_message(dummy_user, dummy_chat, f"Msg repeat {i}")
            res = await gate(handler, msg, {})
            assert res == "OK"

        msg_spam2 = make_message(dummy_user, dummy_chat, "Spam repeat 6")
        res2 = await gate(handler, msg_spam2, {})
        assert res2 is None
        assert gate.is_user_banned(dummy_user.id)
        assert "Блокировка на 5 минут" in msg_spam2.answer.call_args[0][0]

        # 5. Simulate 305 seconds later (unbanned, subsequent spam triggers 5 minutes ban again)
        current_simulated_time = 1065.0 + 305.0
        assert not gate.is_user_banned(dummy_user.id)

        for i in range(5):
            msg = make_message(dummy_user, dummy_chat, f"Msg 3rd {i}")
            res = await gate(handler, msg, {})
            assert res == "OK"

        msg_spam3 = make_message(dummy_user, dummy_chat, "Spam 3rd 6")
        res3 = await gate(handler, msg_spam3, {})
        assert res3 is None
        assert gate.is_user_banned(dummy_user.id)
        assert "Блокировка на 5 минут" in msg_spam3.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_callback_query_never_banned_and_works_during_ban(dummy_user, dummy_chat):
    """Verifies that buttons (CallbackQuery) never get banned and work even while user is in spam ban."""
    gate = MessageGateMiddleware(
        throttle_interval=0.0,
        spam_window=3.0,
        spam_max_requests=5,
        first_ban_duration=60.0,
    )
    handler = AsyncMock(return_value="OK")

    # Spam with messages to trigger 60s ban
    for i in range(6):
        msg = make_message(dummy_user, dummy_chat, f"Msg {i}")
        await gate(handler, msg, {})

    assert gate.is_user_banned(dummy_user.id)

    # Now click an inline button while banned: it MUST be allowed
    call = make_callback(dummy_user, dummy_chat, data="faculty|0")
    res = await gate(handler, call, {})
    assert res == "OK"
    assert handler.call_count == 6  # 5 valid messages + 1 callback query

    # Rapid button clicks must not trigger ban either
    for _ in range(10):
        c = make_callback(dummy_user, dummy_chat, data="schedule|0")
        res_c = await gate(handler, c, {})
        assert res_c == "OK"


@pytest.mark.asyncio
async def test_message_gate_media_rejection_kazakh(dummy_user, dummy_chat):
    """Verifies that non-text messages return Kazakh message when user language is kz."""
    gate = MessageGateMiddleware(throttle_interval=0.0)
    handler = AsyncMock(return_value="OK")

    animation = Animation(
        file_id="anim1",
        file_unique_id="au1",
        width=100,
        height=100,
        duration=5,
    )
    msg = make_message(dummy_user, dummy_chat, text=None)
    object.__setattr__(msg, "animation", animation)

    with patch("src.services.user_service.user_service.get_user_language", AsyncMock(return_value="kz")), \
         patch("src.bot.middlewares.user_action_logger.user_action_logging_middleware.log_action", AsyncMock()) as mock_log:
        res = await gate(handler, msg, {})
        assert res is None
        assert msg.answer.call_count == 1
        assert msg.answer.call_args[0][0] == "Мен мұны оқи алмаймын"
        mock_log.assert_awaited_once()
        args, _ = mock_log.call_args
        assert args[2] == "media_rejected"
        assert "[Медиа: GIF / анимация]" in args[3]


@pytest.mark.asyncio
async def test_media_flood_counts_towards_anti_spam(dummy_user, dummy_chat):
    """Verifies that sending 5 non-text media items in 3s triggers spam ban on the 6th."""
    gate = MessageGateMiddleware(
        throttle_interval=0.0,
        spam_window=3.0,
        spam_max_requests=5,
        first_ban_duration=60.0,
    )
    handler = AsyncMock(return_value="OK")

    photo = [PhotoSize(file_id="p1", file_unique_id="pu1", width=100, height=100)]

    # 5 GIFs/photos within 3s
    for i in range(5):
        msg = make_message(dummy_user, dummy_chat, text=None, photo=photo)
        await gate(handler, msg, {})
        assert msg.answer.call_args[0][0] == "Я не могу это прочитать"

    assert not gate.is_user_banned(dummy_user.id)

    # 6th photo triggers ban!
    msg6 = make_message(dummy_user, dummy_chat, text=None, photo=photo)
    res6 = await gate(handler, msg6, {})
    assert res6 is None
    assert gate.is_user_banned(dummy_user.id)
    assert "Блокировка на 60 секунд" in msg6.answer.call_args[0][0]


def test_dispatcher_registers_gate_as_outer_middleware():
    """Verifies that create_dispatcher registers MessageGateMiddleware as message outer_middleware."""
    from src.bot.bot_instance import create_dispatcher
    gate = MessageGateMiddleware()
    dp = create_dispatcher(message_gate=gate)
    assert gate in dp.message.outer_middleware


@pytest.mark.asyncio
async def test_rapid_alternation_media_and_buttons(dummy_user, dummy_chat):
    """Verifies that alternating between media messages and button clicks keeps buttons 100% snappy."""
    gate = MessageGateMiddleware(throttle_interval=0.0)
    handler = AsyncMock(return_value="OK")
    photo = [PhotoSize(file_id="p1", file_unique_id="pu1", width=100, height=100)]

    for i in range(4):
        # 1. Non-text media message: rejected
        msg = make_message(dummy_user, dummy_chat, text=None, photo=photo)
        res_msg = await gate(handler, msg, {})
        assert res_msg is None
        assert msg.answer.call_args[0][0] == "Я не могу это прочитать"

        # 2. Inline button click: immediate success
        call = make_callback(dummy_user, dummy_chat, data=f"faculty|{i}")
        res_call = await gate(handler, call, {})
        assert res_call == "OK"

    assert handler.call_count == 4  # All 4 callbacks executed cleanly


@pytest.mark.asyncio
async def test_message_gate_kazakh_spam_ban_warning(dummy_user, dummy_chat):
    """Verifies that Kazakh language users receive spam warning in Kazakh."""
    gate = MessageGateMiddleware(
        throttle_interval=0.0,
        spam_window=3.0,
        spam_max_requests=5,
        first_ban_duration=60.0,
    )
    handler = AsyncMock(return_value="OK")

    with patch("src.services.user_service.user_service.get_user_language", AsyncMock(return_value="kz")):
        for i in range(5):
            msg = make_message(dummy_user, dummy_chat, f"Msg {i}")
            await gate(handler, msg, {})

        msg6 = make_message(dummy_user, dummy_chat, "Msg 6")
        res6 = await gate(handler, msg6, {})
        assert res6 is None
        assert gate.is_user_banned(dummy_user.id)
        assert "60 секундқа бұғаттау" in msg6.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_anti_spam_resets_after_repeat_window_expires(dummy_user, dummy_chat):
    """Verifies that if spam happens after the 5-minute repeat window expires, it resets to 1st strike (60s)."""
    gate = MessageGateMiddleware(
        throttle_interval=0.0,
        spam_window=3.0,
        spam_max_requests=5,
        first_ban_duration=60.0,
        repeat_window=300.0,
        repeat_ban_duration=300.0,
    )
    handler = AsyncMock(return_value="OK")

    simulated_time = 1000.0
    with patch("time.time", side_effect=lambda: simulated_time):
        # 1st strike (60s ban)
        for i in range(6):
            msg = make_message(dummy_user, dummy_chat, f"Msg {i}")
            await gate(handler, msg, {})
        assert gate.is_user_banned(dummy_user.id)
        assert gate._banned_users[dummy_user.id] == 1060.0

        # Fast-forward 600 seconds (> 300s repeat window after ban expired at 1060.0)
        simulated_time = 1700.0
        assert not gate.is_user_banned(dummy_user.id)

        # Spam again -> must reset to 1st strike (60s ban), NOT repeat 300s ban!
        for i in range(5):
            msg = make_message(dummy_user, dummy_chat, f"Msg {i}")
            await gate(handler, msg, {})
        msg_spam = make_message(dummy_user, dummy_chat, "Msg 6")
        await gate(handler, msg_spam, {})
        assert gate.is_user_banned(dummy_user.id)
        assert gate._banned_users[dummy_user.id] == 1760.0  # 60s, not 300s!
        assert "Блокировка на 60 секунд" in msg_spam.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_is_user_banned_concurrent_expiration_safe():
    """Verifies that is_user_banned does not raise KeyError on concurrent expirations."""
    gate = MessageGateMiddleware()
    user_id = 12345
    gate._banned_users[user_id] = 50.0

    with patch("time.time", return_value=100.0):
        # 1st call clears the key
        assert not gate.is_user_banned(user_id)
        # 2nd call should cleanly return False without KeyError
        assert not gate.is_user_banned(user_id)


@pytest.mark.asyncio
async def test_check_has_prior_button_ignores_media_rejected():
    """Verifies that non-button events (media_rejected, spam_blocked) do not count as prior button clicks."""
    from src.bot.middlewares.user_action_logger import user_action_logging_middleware
    user_action_logging_middleware._queue.clear()

    # If queue has media_rejected and spam_blocked:
    user_action_logging_middleware._queue.append({"user_id": 999111, "action": "media_rejected"})
    user_action_logging_middleware._queue.append({"user_id": 999111, "action": "spam_blocked"})
    user_action_logging_middleware._queue.append({"user_id": 999111, "action": "long_text_rejected"})

    with patch("src.database.models.user_action.UserAction.find_one", AsyncMock(return_value=None)):
        has_prior = await user_action_logging_middleware._check_has_prior_button(999111)
        assert not has_prior  # Must be False, media/spam is NOT a button

    user_action_logging_middleware._queue.clear()


@pytest.mark.asyncio
async def test_user_action_logger_flush_no_deadlock(mock_db):
    """Verifies that flush() does not deadlock when invoked re-entrantly or from background log tasks."""
    from src.bot.middlewares.user_action_logger import UserActionLoggingMiddleware

    logger_mid = UserActionLoggingMiddleware()
    logger_mid._queue.append({
        "user_id": 111,
        "username": "user1",
        "action": "test",
        "text": "sample",
        "is_first": False,
        "created_at": datetime.now(timezone.utc),
    })

    # Spawn a background task that itself calls flush()
    async def _worker_calling_flush():
        await asyncio.sleep(0.01)
        await logger_mid.flush()

    task = asyncio.create_task(_worker_calling_flush())
    logger_mid._bg_log_tasks.add(task)

    # Calling flush directly concurrently should not deadlock or raise
    await asyncio.wait_for(logger_mid.flush(), timeout=2.0)
    await asyncio.wait_for(task, timeout=2.0)
    assert len(logger_mid._queue) == 0
    logger_mid.clear_cache()





