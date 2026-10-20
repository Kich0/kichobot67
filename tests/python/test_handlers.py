"""Unit tests for Phase 5 Handlers and Routers: commands, callbacks, search, and admin."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram.types import CallbackQuery, Chat, Message, ReplyKeyboardRemove, User

from src.bot.handlers.admin_router import (
    _parse_duration,
    handle_ahelp,
    handle_ban_command,
    handle_bans_list,
    handle_get_group,
    handle_get_user,
    handle_info,
    handle_mute_command,
    handle_sms,
    handle_stat,
    handle_sync_new_data,
    handle_unban_command,
    handle_unmute_command,
    handle_user_logs,
    handle_users_count,
)
from src.bot.handlers.callbacks_router import (
    clear_schedule_caches,
    get_or_fetch_student_schedule,
    get_or_fetch_teacher_text_schedule,
    handle_back_to_start,
    handle_delete,
    handle_faculty_menu,
    handle_nothing,
    handle_set_language,
)
from src.bot.handlers.commands_router import (
    handle_group_search_prefix,
    handle_help,
    handle_new_schedule,
    handle_profile,
    handle_schedule_command,
    handle_settings,
    handle_start,
    handle_teacher_search_prefix,
)
from src.bot.handlers.search_router import handle_text_search
from src.database.models.black_list import BlackList
from src.database.models.faculty import Faculty
from src.database.models.group import Group
from src.database.models.schedule import Schedule, ScheduleDay, SubjectItem
from src.database.models.teacher import Teacher
from src.database.models.teacher_schedule import TeacherLessonItem, TeacherSchedule, TeacherScheduleDay
from src.database.models.user import User as UserModel
from src.services.smart_search import (
    FuzzySuggestion,
    SearchResultType,
    SmartSearchResult,
    smart_search_service,
)
from src.services.user_service import user_service


@pytest.fixture(autouse=True)
def clean_caches():
    clear_schedule_caches()
    yield
    clear_schedule_caches()


def make_message(user_id=12345, text="/start", username="testuser", is_admin=False):
    u = User(id=user_id, is_bot=False, first_name="Тест", username=username)
    c = Chat(id=user_id, type="private")
    msg = Message(
        message_id=10,
        date=datetime.now(timezone.utc),
        chat=c,
        from_user=u,
        text=text,
    )
    mock_answer = AsyncMock()
    mock_delete = AsyncMock()
    mock_edit = AsyncMock()
    object.__setattr__(msg, "answer", mock_answer)
    object.__setattr__(msg, "delete", mock_delete)
    object.__setattr__(msg, "edit_text", mock_edit)

    mock_bot = AsyncMock()
    object.__setattr__(msg, "_bot", mock_bot)
    return msg


def make_callback(user_id=12345, data="start", username="testuser"):
    msg = make_message(user_id=user_id, username=username)
    u = msg.from_user
    call = CallbackQuery(
        id="call_99",
        from_user=u,
        chat_instance="1",
        message=msg,
        data=data,
    )
    mock_answer = AsyncMock()
    object.__setattr__(call, "answer", mock_answer)
    object.__setattr__(call, "_bot", msg.bot)
    return call


@pytest.mark.asyncio
async def test_commands_start_registers_user(mock_db):
    msg = make_message(user_id=88001, text="/start", username="newbie")
    await handle_start(msg)

    assert msg.answer.call_count == 1
    args, kwargs = msg.answer.call_args
    assert "Выберите язык интерфейса" in args[0]
    assert "reply_markup" in kwargs

    # Verify user saved in DB
    user_doc = await UserModel.find_one({"userId": 88001})
    assert user_doc is not None
    assert user_doc.username == "newbie"


@pytest.mark.asyncio
async def test_commands_help_and_profile(mock_db):
    # Register user first
    user_doc = UserModel(userId=88002, username="student1", group=501, language="ru")
    await user_doc.insert()

    # /help
    msg_help = make_message(user_id=88002, text="/help")
    await handle_help(msg_help)
    assert msg_help.answer.call_count == 1
    assert "Как пользоваться ботом" in msg_help.answer.call_args[0][0]

    # /profile
    msg_prof = make_message(user_id=88002, text="/profile")
    await handle_profile(msg_prof)
    assert msg_prof.answer.call_count == 1
    prof_text = msg_prof.answer.call_args[0][0]
    assert "88002" in prof_text
    assert "Русский" in prof_text


@pytest.mark.asyncio
async def test_commands_settings_and_new(mock_db):
    msg = make_message(user_id=88003, text="/settings")
    await handle_settings(msg)
    assert msg.answer.call_count == 1
    assert "reply_markup" in msg.answer.call_args[1]

    # Insert sample faculty
    f = Faculty(id=1, name="ФИТ", fullName="Факультет информационных технологий")
    await f.insert()

    msg_new = make_message(user_id=88003, text="/new")
    await handle_new_schedule(msg_new)
    assert msg_new.answer.call_count == 1
    assert "Чье расписание тебе нужно?" in msg_new.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_callbacks_navigation_and_language(mock_db):
    # Test nothing callback
    call_nothing = make_callback(data="nothing")
    await handle_nothing(call_nothing)
    assert call_nothing.answer.call_count == 1

    # Test delete callback
    call_delete = make_callback(data="delete")
    await handle_delete(call_delete)
    assert call_delete.answer.call_count == 1
    assert call_delete.message.delete.call_count == 1

    # Test start callback
    call_start = make_callback(user_id=88004, data="start")
    await handle_back_to_start(call_start)
    assert call_start.message.edit_text.call_count == 1

    # Test language switch callback
    call_lang = make_callback(user_id=88004, data="languageIskz")
    await handle_set_language(call_lang)
    assert call_lang.message.delete.call_count == 1
    assert call_lang.message.answer.call_count == 1
    assert "Сәлем!" in call_lang.message.answer.call_args[0][0]

    # Verify updated in DB
    user_doc = await UserModel.find_one({"userId": 88004})
    assert user_doc is not None
    assert user_doc.language == "kz"


@pytest.mark.asyncio
async def test_callbacks_faculty_menu(mock_db):
    f = Faculty(id=1, name="ФИТ")
    await f.insert()

    call_fac = make_callback(user_id=88005, data="faculty|0")
    await handle_faculty_menu(call_fac)
    assert call_fac.message.edit_text.call_count == 1
    assert "Выбор факультета" in call_fac.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_search_router_exact_group_and_teacher(mock_db):
    mock_result_group = SmartSearchResult(
        query="ИС-21-1",
        result_type=SearchResultType.EXACT_GROUP,
        exact_group={"id": 501, "name": "ИС-21-1", "language": "ru"},
    )
    mock_group_schedule = {
        "records": [
            {
                "dayIndex": 1,
                "time": "08:30-09:20",
                "slot": 1,
                "subject": "Базы данных",
                "type": "Лекция",
                "room": "301",
                "building": "1",
                "teacher": "Жунусова А. К.",
            }
        ],
        "total": 1,
    }

    with patch.object(smart_search_service, "search", AsyncMock(return_value=mock_result_group)), \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", AsyncMock(return_value=mock_group_schedule)):
        msg = make_message(user_id=88006, text="ИС-21-1")
        await handle_text_search(msg)
        assert msg.answer.call_count == 1
        assert "ИС-21-1" in msg.answer.call_args[0][0]

    mock_result_teacher = SmartSearchResult(
        query="Жунусова",
        result_type=SearchResultType.EXACT_TEACHER,
        exact_teacher={"id": 305, "fullName": "Жунусова А. К."},
    )
    mock_teacher_schedule = {
        "records": [
            {
                "dayIndex": 1,
                "time": "08:30-09:20",
                "slot": 1,
                "group": "ИС-21-1",
                "subject": "Базы данных",
                "type": "Лекция",
                "room": "301",
                "building": "1",
            }
        ],
        "total": 1,
    }

    with patch.object(smart_search_service, "search", AsyncMock(return_value=mock_result_teacher)), \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=mock_teacher_schedule)):
        msg = make_message(user_id=88006, text="Жунусова")
        await handle_text_search(msg)
        assert msg.answer.call_count == 1
        assert "Жунусова А. К." in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_search_router_fuzzy_and_not_found():
    # Fuzzy suggestions
    mock_fuzzy = SmartSearchResult(
        query="Жунусва",
        result_type=SearchResultType.FUZZY_SUGGESTIONS,
        fuzzy_suggestions=[
            FuzzySuggestion(type="teacher", item={"id": 305, "fullName": "Жунусова А. К."}, similarity=0.9)
        ],
    )
    with patch.object(smart_search_service, "search", AsyncMock(return_value=mock_fuzzy)):
        msg = make_message(user_id=88007, text="Жунусва")
        await handle_text_search(msg)
        assert msg.answer.call_count == 1
        assert "Возможно, вы имели в виду" in msg.answer.call_args[0][0]

    # Not found
    mock_none = SmartSearchResult(
        query="НеизвестныйЗапрос123",
        result_type=SearchResultType.NOT_FOUND,
    )
    with patch.object(smart_search_service, "search", AsyncMock(return_value=mock_none)):
        msg = make_message(user_id=88007, text="НеизвестныйЗапрос123")
        await handle_text_search(msg)
        assert msg.answer.call_count == 1
        assert "ничего не найдено" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_search_router_single_letter_query():
    """Verifies that single-letter query like 'Ю' in chat is processed and returns search results."""
    mock_result_single = SmartSearchResult(
        query="Ю",
        result_type=SearchResultType.COMBINED,
        groups=[{"id": 1, "name": "Ю-26-1к"}, {"id": 2, "name": "Ю-25-2р"}],
        teachers=[{"id": 10, "name": "Юсупова А.", "fullName": "Юсупова Асель"}],
        prefer_groups_first=True,
    )
    with patch.object(smart_search_service, "search", AsyncMock(return_value=mock_result_single)):
        msg = make_message(user_id=88008, text="Ю")
        await handle_text_search(msg)
        assert msg.answer.call_count == 1
        assert "найдено совпадений: <b>3</b>" in msg.answer.call_args[0][0]
        markup = msg.answer.call_args[1]["reply_markup"]
        btn_texts = [btn.text for row in markup.inline_keyboard for btn in row]
        assert any("Группы (2)" in t for t in btn_texts)
        assert any("Преподаватели (1)" in t for t in btn_texts)
        assert any("Ю-26-1к" in t for t in btn_texts)


@pytest.mark.asyncio
async def test_admin_router_security(mock_db):
    # Regular user is rejected
    non_admin_msg = make_message(user_id=99001, text="/stat")
    await handle_stat(non_admin_msg)
    assert non_admin_msg.answer.call_count == 1
    assert "нет доступа" in non_admin_msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_admin_router_commands_as_admin(mock_db):
    admin_id = 99999
    admin_user = UserModel(userId=admin_id, username="superadmin", isAdmin=True)
    await admin_user.insert()

    # /stat
    msg_stat = make_message(user_id=admin_id, text="/stat")
    await handle_stat(msg_stat)
    assert msg_stat.answer.call_count == 1
    assert "Статистика ежедневного онлайна:" in msg_stat.answer.call_args[0][0]

    # /users
    msg_users = make_message(user_id=admin_id, text="/users")
    await handle_users_count(msg_users)
    assert msg_users.answer.call_count == 1
    assert "пользователей" in msg_users.answer.call_args[0][0]

    # /sync runs asynchronously without blocking
    msg_sync = make_message(user_id=admin_id, text="/sync")
    await handle_sync_new_data(msg_sync)
    assert msg_sync.answer.call_count >= 1
    assert "Синхронизация запущена" in msg_sync.answer.call_args[0][0]

    # /ahelp
    msg_ahelp = make_message(user_id=admin_id, text="/ahelp")
    await handle_ahelp(msg_ahelp)
    assert msg_ahelp.answer.call_count == 1
    assert "КОМАНДЫ АДМИНИСТРАТОРА" in msg_ahelp.answer.call_args[0][0]

    # /ban and /unban
    target = UserModel(userId=777, username="troublemaker")
    await target.insert()

    msg_ban = make_message(user_id=admin_id, text="/ban 777 Спам")
    await handle_ban_command(msg_ban)
    assert msg_ban.answer.call_count == 1
    assert "заблокирован" in msg_ban.answer.call_args[0][0]

    # Check blacklist
    restriction = await BlackList.find_one({"userId": 777})
    assert restriction is not None
    assert restriction.type == "ban"

    # /bans
    msg_bans = make_message(user_id=admin_id, text="/bans")
    await handle_bans_list(msg_bans)
    assert msg_bans.answer.call_count == 1
    assert "777" in msg_bans.answer.call_args[0][0]

    # /unban
    msg_unban = make_message(user_id=admin_id, text="/unban 777")
    await handle_unban_command(msg_unban)
    assert msg_unban.answer.call_count == 1
    assert "сняты" in msg_unban.answer.call_args[0][0]

    assert await BlackList.find_one({"userId": 777}) is None


@pytest.mark.asyncio
async def test_commands_help_regex_triggers(mock_db):
    for trigger in ["Помощь", "Көмек", "❓ Помощь", "Возникла проблема", "Қате", "Мәселе", "произошла ошибка"]:
        msg = make_message(user_id=88010, text=trigger)
        await handle_help(msg)
        assert msg.answer.call_count == 1
        assert "Как пользоваться ботом" in msg.answer.call_args[0][0] or "Қолдану" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_commands_schedule_routing(mock_db):
    from src.bot.handlers.callbacks_router import get_current_day_number
    curr_day_idx = get_current_day_number() + 1

    # 1. Student routing
    grp = Group(id=501, name="ИС-21-1")
    await grp.insert()
    student_user = UserModel(userId=88020, username="student_user", scheduleType="student", group=501, language="ru")
    await student_user.insert()

    mock_student_schedule = {
        "records": [
            {"dayIndex": curr_day_idx, "time": "8.30-9.20", "slot": 1, "subject": "Вышмат", "type": "Лекция", "room": "202", "building": "1"}
        ]
    }
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", AsyncMock(return_value=mock_student_schedule)):
        msg = make_message(user_id=88020, text="/schedule")
        await handle_schedule_command(msg)
        assert msg.answer.call_count == 1
        assert "Вышмат" in msg.answer.call_args[0][0]

    # 2. Teacher routing
    tch = Teacher(id=305, name="Жунусова А. К.")
    await tch.insert()
    teacher_user = UserModel(userId=88021, username="teacher_user", scheduleType="teacher", teacher=305, language="ru")
    await teacher_user.insert()

    mock_teacher_schedule = {
        "records": [
            {"dayIndex": curr_day_idx, "time": "8.30-9.20", "slot": 1, "group": "ИС-21", "subject": "Программирование", "type": "Прак.зан.", "room": "205", "building": "2"}
        ]
    }
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=mock_teacher_schedule)):
        msg = make_message(user_id=88021, text="расписание")
        await handle_schedule_command(msg)
        assert msg.answer.call_count == 1
        assert "Программирование" in msg.answer.call_args[0][0]

    # 3. No preferences -> redirected to new schedule
    guest_user = UserModel(userId=88022, username="guest_user")
    await guest_user.insert()
    msg = make_message(user_id=88022, text="/schedule")
    await handle_schedule_command(msg)
    assert msg.answer.call_count == 1
    assert "Чье расписание тебе нужно?" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_commands_search_prefix_shortcuts(mock_db):
    from src.bot.handlers.callbacks_router import get_current_day_number
    curr_day_idx = get_current_day_number() + 1

    # 1. Bare group prefix error
    msg_bare_g = make_message(user_id=88030, text="Г")
    await handle_group_search_prefix(msg_bare_g)
    assert msg_bare_g.answer.call_count == 1
    assert "как минимум из 2 символов" in msg_bare_g.answer.call_args[0][0]

    # 2. Group prefix match
    mock_groups = [{"id": 501, "name": "ИС-21-1", "language": "ru"}]
    mock_student_schedule = {
        "records": [
            {"dayIndex": curr_day_idx, "time": "8.30-9.20", "slot": 1, "subject": "Архитектура ЭВМ", "type": "Лекция", "room": "101", "building": "1"}
        ]
    }
    with patch.object(smart_search_service, "get_all_groups", AsyncMock(return_value=mock_groups)), \
         patch.object(smart_search_service, "find_groups", MagicMock(return_value=mock_groups)), \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", AsyncMock(return_value=mock_student_schedule)):
        msg_g = make_message(user_id=88030, text="Г ИС-21-1")
        await handle_group_search_prefix(msg_g)
        assert msg_g.answer.call_count == 1
        assert "Архитектура ЭВМ" in msg_g.answer.call_args[0][0]

    # 3. Bare teacher prefix error
    msg_bare_p = make_message(user_id=88030, text="П")
    await handle_teacher_search_prefix(msg_bare_p)
    assert msg_bare_p.answer.call_count == 1
    assert "как минимум из 2 символов" in msg_bare_p.answer.call_args[0][0]

    # 4. Teacher prefix match
    mock_teachers = [{"id": 305, "name": "Жунусова А. К.", "fullName": "Жунусова А. К."}]
    mock_teacher_schedule = {
        "records": [
            {"dayIndex": curr_day_idx, "time": "8.30-9.20", "slot": 1, "group": "ИС-21", "subject": "Алгоритмы", "type": "Лекция", "room": "102", "building": "1"}
        ]
    }
    with patch.object(smart_search_service, "get_all_teachers", AsyncMock(return_value=mock_teachers)), \
         patch.object(smart_search_service, "find_teachers", MagicMock(return_value=mock_teachers)), \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=mock_teacher_schedule)):
        msg_p = make_message(user_id=88030, text="П Жунусова")
        await handle_teacher_search_prefix(msg_p)
        assert msg_p.answer.call_count == 1
        assert "Алгоритмы" in msg_p.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_compact_admin_commands_and_mute(mock_db):
    admin_id = 99999
    admin_user = UserModel(userId=admin_id, username="superadmin", isAdmin=True)
    await admin_user.insert()

    target_user = UserModel(userId=778, username="test_target", group=501)
    await target_user.insert()

    # /get_user778 compact format
    msg_get_user = make_message(user_id=admin_id, text="/get_user778")
    await handle_get_user(msg_get_user)
    assert msg_get_user.answer.call_count == 1
    assert "test_target" in msg_get_user.answer.call_args[0][0]

    # /user_logs778 compact format
    msg_logs = make_message(user_id=admin_id, text="/user_logs778 10")
    await handle_user_logs(msg_logs)
    assert msg_logs.answer.call_count == 1

    # /get_group501 compact format
    grp = Group(id=501, name="ИС-21-1")
    await grp.insert()
    msg_grp = make_message(user_id=admin_id, text="/get_group501")
    await handle_get_group(msg_grp)
    assert msg_grp.answer.call_count == 1
    assert "ИС-21-1" in msg_grp.answer.call_args[0][0]

    # /mute 778
    msg_mute = make_message(user_id=admin_id, text="/mute 778 60 Флуд")
    await handle_mute_command(msg_mute)
    assert msg_mute.answer.call_count == 1
    assert "выдан мут" in msg_mute.answer.call_args[0][0]

    # Check BlackList for mute
    entry = await BlackList.find_one({"userId": 778})
    assert entry is not None
    assert entry.type == "mute"

    # /unmute 778
    msg_unmute = make_message(user_id=admin_id, text="/unmute 778")
    await handle_unmute_command(msg_unmute)
    assert msg_unmute.answer.call_count == 1
    assert "сняты" in msg_unmute.answer.call_args[0][0]
    assert await BlackList.find_one({"userId": 778}) is None


@pytest.mark.asyncio
async def test_mongodb_pydantic_model_fallback_without_error(mock_db):
    # Student Schedule fallback with Pydantic ScheduleDay & SubjectItem
    sched_doc = Schedule(
        groupId=601,
        data=[
            ScheduleDay(
                day="Понедельник",
                subjects=[
                    SubjectItem(time="8.30-9.20", subject="Физика"),
                ],
            )
        ],
    )
    await sched_doc.insert()

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", AsyncMock(side_effect=Exception("API Down"))):
        cached = await get_or_fetch_student_schedule(601, "ru", is_refresh=True)
        assert cached is not None
        assert cached["is_from_db"] is True
        assert isinstance(cached["data"], list)
        assert cached["data"][0]["day"] == "Понедельник"
        assert cached["data"][0]["subjects"][0]["subject"] == "Физика"

    # Teacher Schedule fallback with Pydantic TeacherScheduleDay & TeacherLessonItem
    teacher_sched_doc = TeacherSchedule(
        teacherId=401,
        data=[
            TeacherScheduleDay(
                day="Вторник",
                groups=[
                    TeacherLessonItem(time="10.00-10.50", group="ИС-21", subject="Химия", lessonType="Лекция"),
                ],
            )
        ],
    )
    await teacher_sched_doc.insert()

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(side_effect=Exception("API Down"))):
        cached_t = await get_or_fetch_teacher_text_schedule(401, "ru", is_refresh=True)
        assert cached_t is not None
        assert cached_t["is_from_db"] is True
        assert isinstance(cached_t["data"], list)
        assert cached_t["data"][0]["day"] in ("Вторник", "Дүйсенбі")


@pytest.mark.asyncio
async def test_reply_menu_button_deletes_incoming_message_and_removes_keyboard(mock_db):
    from src.bot.handlers.commands_router import handle_group_menu, handle_teacher_menu

    # 1. '🗒 Новое расписание'
    msg = make_message(text="🗒 Новое расписание")
    await handle_new_schedule(msg)
    msg.delete.assert_awaited_once()
    assert msg.answer.call_count == 1
    assert msg._bot.send_message.call_count == 1
    bot_call_kwargs = msg._bot.send_message.call_args[1]
    assert isinstance(bot_call_kwargs["reply_markup"], ReplyKeyboardRemove)

    # 2. '🗓 Расписание студента'
    msg_grp = make_message(text="🗓 Расписание студента")
    await handle_group_menu(msg_grp)
    msg_grp.delete.assert_awaited_once()
    assert msg_grp._bot.send_message.call_count == 1
    assert isinstance(msg_grp._bot.send_message.call_args[1]["reply_markup"], ReplyKeyboardRemove)

    # 3. '🗓 Расписание преподавателя'
    msg_tch = make_message(text="🗓 Расписание преподавателя")
    await handle_teacher_menu(msg_tch)
    msg_tch.delete.assert_awaited_once()
    assert msg_tch._bot.send_message.call_count == 1
    assert isinstance(msg_tch._bot.send_message.call_args[1]["reply_markup"], ReplyKeyboardRemove)


@pytest.mark.asyncio
async def test_help_command_deletes_incoming_message_and_attaches_reply_keyboard_remove(mock_db):
    # /help
    msg = make_message(text="/help")
    await handle_help(msg)
    msg.delete.assert_awaited_once()
    assert msg.answer.call_count == 1
    answer_kwargs = msg.answer.call_args[1]
    assert isinstance(answer_kwargs["reply_markup"], ReplyKeyboardRemove)

    # Reply button '❓ Помощь'
    msg_btn = make_message(text="❓ Помощь")
    await handle_help(msg_btn)
    msg_btn.delete.assert_awaited_once()
    assert msg_btn.answer.call_count == 1
    assert isinstance(msg_btn.answer.call_args[1]["reply_markup"], ReplyKeyboardRemove)


@pytest.mark.asyncio
async def test_search_action_logging_integration(mock_db):
    from src.bot.middlewares.user_action_logger import KNOWN_GROUPS, user_action_logging_middleware

    grp = Group(id=5555, name="ЮР-21", language="ru")
    await grp.insert()

    msg = make_message(text="ЮР-21")
    with patch.object(user_action_logging_middleware, "log_action", MagicMock()) as mock_log, \
         patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", AsyncMock(return_value={"records": []})):
        await handle_text_search(msg)
        assert mock_log.call_count == 1
        args, _ = mock_log.call_args
        assert args[2] == "search_exact_group"
        assert 'Поиск группы "ЮР-21": найдена группа "ЮР-21"' in args[3]
        assert KNOWN_GROUPS.get(5555) == "ЮР-21"


@pytest.mark.asyncio
async def test_admin_sync_triggers_real_data_update(mock_db):
    """Verifies that /sync triggers run_daily_data_update in background."""
    from src.database.models.user import User as UserModel

    admin_id = 99991
    await UserModel(userId=admin_id, username="admin_sync", isAdmin=True).insert()
    msg = make_message(user_id=admin_id, text="/sync")
    with patch("src.cron.daily_update.run_daily_data_update", AsyncMock()) as mock_update:
        await handle_sync_new_data(msg)
        assert msg.answer.call_count >= 1
        assert "Синхронизация запущена" in msg.answer.call_args[0][0]
        # Wait briefly for background task
        await asyncio.sleep(0.05)
        assert mock_update.await_count == 1


@pytest.mark.asyncio
async def test_admin_ban_and_mute_with_duration(mock_db):
    """Verifies that /ban and /mute correctly parse durations and store until timestamps."""
    from datetime import timedelta
    from src.database.models.user import User as UserModel
    from src.database.models.black_list import BlackList

    admin_id = 99992
    await UserModel(userId=admin_id, username="admin_mod", isAdmin=True).insert()
    target_ban = UserModel(userId=8881, username="spammer8881")
    await target_ban.insert()
    target_mute = UserModel(userId=8882, username="spammer8882")
    await target_mute.insert()

    # 1. Ban for 30 minutes
    now_before = datetime.now(timezone.utc)
    msg_ban = make_message(user_id=admin_id, text="/ban 8881 30m Спам реклама")
    await handle_ban_command(msg_ban)
    assert msg_ban.answer.call_count == 1
    assert "заблокирован" in msg_ban.answer.call_args[0][0]

    banned_rec = await BlackList.find_one({"userId": 8881})
    assert banned_rec is not None
    assert banned_rec.type == "ban"
    assert banned_rec.reason == "Спам реклама"
    assert banned_rec.until is not None
    ban_until_utc = banned_rec.until.replace(tzinfo=timezone.utc) if banned_rec.until.tzinfo is None else banned_rec.until
    assert ban_until_utc > now_before + timedelta(minutes=25)
    assert ban_until_utc < now_before + timedelta(minutes=35)

    # 2. Mute for 2 hours
    msg_mute = make_message(user_id=admin_id, text="/mute 8882 2h Флуд в чате")
    await handle_mute_command(msg_mute)
    assert msg_mute.answer.call_count == 1
    assert "выдан мут" in msg_mute.answer.call_args[0][0]

    muted_rec = await BlackList.find_one({"userId": 8882})
    assert muted_rec is not None
    assert muted_rec.type == "mute"
    assert muted_rec.reason == "Флуд в чате"
    assert muted_rec.until is not None
    mute_until_utc = muted_rec.until.replace(tzinfo=timezone.utc) if muted_rec.until.tzinfo is None else muted_rec.until
    assert mute_until_utc > now_before + timedelta(hours=1, minutes=50)
    assert mute_until_utc < now_before + timedelta(hours=2, minutes=10)


@pytest.mark.asyncio
async def test_admin_info_displays_health_metrics(mock_db):
    """Verifies that /info displays accurate uptime and memory from HealthMonitor."""
    from src.database.models.user import User as UserModel
    from src.api.health import health_monitor

    admin_id = 99993
    await UserModel(userId=admin_id, username="admin_info", isAdmin=True).insert()
    msg = make_message(user_id=admin_id, text="/info")

    with patch.object(health_monitor, "get_status", AsyncMock(return_value={
        "uptime": 1234,
        "uptime_seconds": 1234,
        "memory_mb": 142.5,
        "memory": {"rss_mb": 142.5},
        "buketov_api": "CLOSED",
    })):
        await handle_info(msg)
        assert msg.answer.call_count == 1
        output = msg.answer.call_args[0][0]
        assert "1234 сек." in output
        assert "142.5 MB" in output
        assert "CLOSED" in output
        assert "Прогрев:" in output


@pytest.mark.asyncio
async def test_admin_ban_mute_subaction_and_russian_durations(mock_db):
    """Verifies that /ban supports 'mute' subaction, Russian units, and two-token durations."""
    from datetime import timedelta
    from src.database.models.user import User as UserModel
    from src.database.models.black_list import BlackList

    admin_id = 99994
    await UserModel(userId=admin_id, username="admin_mod2", isAdmin=True).insert()
    u1 = UserModel(userId=8891, username="target8891")
    await u1.insert()
    u2 = UserModel(userId=8892, username="target8892")
    await u2.insert()
    u3 = UserModel(userId=8893, username="target8893")
    await u3.insert()

    now_before = datetime.now(timezone.utc)

    # 1. /ban with mute subaction and Russian duration: "mute 30мин"
    msg1 = make_message(user_id=admin_id, text="/ban 8891 mute 30мин Спам реклама")
    await handle_ban_command(msg1)
    assert "выдан мут" in msg1.answer.call_args[0][0]
    rec1 = await BlackList.find_one({"userId": 8891})
    assert rec1 is not None
    assert rec1.type == "mute"
    assert rec1.reason == "Спам реклама"
    assert rec1.until is not None
    rec1_until = rec1.until.replace(tzinfo=timezone.utc) if rec1.until.tzinfo is None else rec1.until
    assert rec1_until > now_before + timedelta(minutes=25)

    # 2. /mute with two-token duration: "2 часа"
    msg2 = make_message(user_id=admin_id, text="/mute 8892 2 часа Флуд в группе")
    await handle_mute_command(msg2)
    assert "выдан мут" in msg2.answer.call_args[0][0]
    rec2 = await BlackList.find_one({"userId": 8892})
    assert rec2 is not None
    assert rec2.type == "mute"
    assert rec2.reason == "Флуд в группе"
    assert rec2.until is not None
    rec2_until = rec2.until.replace(tzinfo=timezone.utc) if rec2.until.tzinfo is None else rec2.until
    assert rec2_until > now_before + timedelta(hours=1, minutes=50)

    # 3. /ban with Russian short unit: "2ч"
    msg3 = make_message(user_id=admin_id, text="/ban 8893 2ч Нарушение правил")
    await handle_ban_command(msg3)
    assert "заблокирован" in msg3.answer.call_args[0][0]
    rec3 = await BlackList.find_one({"userId": 8893})
    assert rec3 is not None
    assert rec3.type == "ban"
    assert rec3.reason == "Нарушение правил"
    assert rec3.until is not None
    rec3_until = rec3.until.replace(tzinfo=timezone.utc) if rec3.until.tzinfo is None else rec3.until
    assert rec3_until > now_before + timedelta(hours=1, minutes=50)


@pytest.mark.asyncio
async def test_admin_bans_list_with_expiry(mock_db):
    """Verifies that /bans displays expiration timestamps for temporary and permanent restrictions."""
    from src.database.models.user import User as UserModel
    from src.database.models.black_list import BlackList
    from src.services.black_list_service import black_list_service

    admin_id = 99995
    await UserModel(userId=admin_id, username="admin_bans", isAdmin=True).insert()

    black_list_service.clear_cache()
    now_dt = datetime.now(timezone.utc) + timedelta(days=2)
    await black_list_service.add_ban(user_id=7701, until=now_dt, reason="Спам", username="temp_user")
    await black_list_service.add_mute(user_id=7702, until=None, reason="Мат", username="perm_user")

    msg = make_message(user_id=admin_id, text="/bans")
    await handle_bans_list(msg)
    assert msg.answer.call_count == 1
    output = msg.answer.call_args[0][0]
    assert "7701" in output
    assert "до" in output
    assert "7702" in output
    assert "бессрочно" in output


@pytest.mark.asyncio
async def test_admin_sync_concurrency_guard(mock_db):
    """Verifies that concurrent /sync requests are blocked by the concurrency lock."""
    from src.database.models.user import User as UserModel

    admin_id = 99996
    await UserModel(userId=admin_id, username="admin_sync_guard", isAdmin=True).insert()

    block_event = asyncio.Event()

    async def _slow_update():
        await block_event.wait()

    msg1 = make_message(user_id=admin_id, text="/sync")
    msg2 = make_message(user_id=admin_id, text="/sync")

    with patch("src.cron.daily_update.run_daily_data_update", _slow_update):
        # First sync starts
        await handle_sync_new_data(msg1)
        assert "Синхронизация запущена" in msg1.answer.call_args[0][0]

        # Second sync while first is running -> blocked
        await handle_sync_new_data(msg2)
        assert "уже выполняется" in msg2.answer.call_args[0][0]

        # Finish first sync
        block_event.set()
        await asyncio.sleep(0.05)



