"""End-to-end integration navigation tests for Student and Teacher flows.

Simulates the exact button click callbacks and verifies:
1. Faculty selection -> correctly fetches programs for ONLY that faculty (not Biology/Geography).
2. Program selection -> correctly fetches groups for ONLY that program.
3. Group selection -> renders the student schedule.
4. Department selection -> fetches teachers for ONLY that department, enriched with full directory info.
5. Teacher selection -> renders teacher table/text schedule.
6. Persistent reply keyboard triggers (/group and /teacher) immediately load user's saved schedule.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from src.core.i18n import t
from src.bot.handlers.callbacks_router import (
    clear_schedule_caches,
    get_current_day_number,
    handle_faculty_menu,
    handle_group_menu,
    handle_program_menu,
    handle_teacher_menu,
)
from src.bot.handlers.commands_router import (
    handle_group_menu as cmd_group_menu,
    handle_new_schedule,
    handle_teacher_menu as cmd_teacher_menu,
)
from src.database.models.department import Department
from src.database.models.faculty import Faculty
from src.database.models.group import Group
from src.database.models.program import Program
from src.database.models.teacher import Teacher
from src.database.models.user import User as UserModel


def make_msg(user_id=99001, text="", username="nav_user"):
    u = User(id=user_id, is_bot=False, first_name="Навигатор", username=username)
    c = Chat(id=user_id, type="private")
    msg = Message(
        message_id=1,
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


def make_call(user_id=99001, data=""):
    msg = make_msg(user_id=user_id)
    u = msg.from_user
    call = CallbackQuery(
        id="call_nav",
        from_user=u,
        chat_instance="nav_inst",
        message=msg,
        data=data,
    )
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", msg.bot)
    return call


@pytest.fixture(autouse=True)
def clean():
    clear_schedule_caches()
    yield
    clear_schedule_caches()


@pytest.mark.asyncio
async def test_student_navigation_flow(mock_db):
    """End-to-end simulation of Student catalog navigation."""
    # Seed DB with two faculties, programs, and groups
    f1 = Faculty(id=1, name="Биолого-географический факультет")
    f2 = Faculty(id=4, name="Факультет математики и информационных технологий")
    await f1.insert()
    await f2.insert()

    p1 = Program(id=10, name="Биология", faculty=1, href="/prog-10")
    p2 = Program(id=40, name="Информационные системы", faculty=4, href="/prog-40")
    await p1.insert()
    await p2.insert()

    g1 = Group(id=100, name="БИО-21-1", program=10, language="ru")
    g2 = Group(id=400, name="ИС-21-1", program=40, language="ru")
    await g1.insert()
    await g2.insert()

    # Step 1: User types /new or clicks '🗒 Новое расписание'
    msg_new = make_msg(user_id=99001, text="/new")
    await handle_new_schedule(msg_new)
    assert msg_new.answer.call_count == 1
    new_kb = msg_new.answer.call_args[1]["reply_markup"]
    new_cbs = [b.callback_data for row in new_kb.inline_keyboard for b in row]
    assert "faculty|0" in new_cbs
    assert "department|0" in new_cbs

    # Step 2: User clicks '🤓 Студент' (callback: 'faculty|0')
    call_fac = make_call(user_id=99001, data="faculty|0")
    await handle_faculty_menu(call_fac)
    assert call_fac.message.edit_text.call_count == 1
    fac_text, fac_kwargs = call_fac.message.edit_text.call_args
    assert "📍 Выбор факультета." in fac_text[0]
    # Check NO duplicate '📍 📍'
    assert "📍 📍" not in fac_text[0]
    fac_kb = fac_kwargs["reply_markup"]
    fac_cbs = [b.callback_data for row in fac_kb.inline_keyboard for b in row]
    assert "program|1|0" in fac_cbs
    assert "program|4|0" in fac_cbs

    # Step 3: User clicks Faculty 4 (callback: 'program|4|0')
    call_prog = make_call(user_id=99001, data="program|4|0")
    await handle_program_menu(call_prog)
    assert call_prog.message.edit_text.call_count == 1
    prog_text, prog_kwargs = call_prog.message.edit_text.call_args
    assert "Информационные системы" in [b.text for row in prog_kwargs["reply_markup"].inline_keyboard for b in row]
    # Verify Biology program is NOT present for faculty 4!
    assert "Биология" not in [b.text for row in prog_kwargs["reply_markup"].inline_keyboard for b in row]
    prog_cbs = [b.callback_data for row in prog_kwargs["reply_markup"].inline_keyboard for b in row]
    assert "group|4|40|0" in prog_cbs

    # Step 4: User clicks Program 40 (callback: 'group|4|40|0')
    call_grp = make_call(user_id=99001, data="group|4|40|0")
    await handle_group_menu(call_grp)
    assert call_grp.message.edit_text.call_count == 1
    grp_text, grp_kwargs = call_grp.message.edit_text.call_args
    grp_cbs = [b.callback_data for row in grp_kwargs["reply_markup"].inline_keyboard for b in row]
    # Verify group 400 is listed, and group 100 is NOT listed!
    assert any("400" in cb for cb in grp_cbs)
    assert not any("100" in cb for cb in grp_cbs)

    # Step 5: Check schedule day view back button points to group list matching commit 153
    from src.bot.handlers.callbacks_router import build_student_day_schedule_view
    cached_sch = {
        "timestamp": 1234567890.0,
        "data": [{"day": "Понедельник", "subjects": [{"subject": "БД", "time": "8.30-9.20"}]}],
        "is_from_db": False,
    }
    _, sch_kb = await build_student_day_schedule_view(cached_sch, group_id=400, sch_lang="ru", day_num=0, ui_lang="ru")
    assert sch_kb.inline_keyboard[1][0].callback_data == "group|4|40|0"


@pytest.mark.asyncio
async def test_teacher_navigation_flow(mock_db):
    """End-to-end simulation of Teacher catalog navigation."""
    d1 = Department(id=5, name="Кафедра ботаники", href="/dept-5")
    d2 = Department(id=12, name="Кафедра прикладной математики и информатики", href="/dept-12")
    await d1.insert()
    await d2.insert()

    t1 = Teacher(id=50, name="Сидоров С. С.", department=5)
    t2 = Teacher(id=120, name="Попова Н. В.", department=12)
    await t1.insert()
    await t2.insert()

    # Step 1: User without saved teacher clicks '🗓 Расписание преподавателя'
    msg_tch_cmd = make_msg(user_id=99002, text="🗓 Расписание преподавателя")
    await cmd_teacher_menu(msg_tch_cmd)
    assert msg_tch_cmd.answer.call_count == 1
    dept_text, dept_kwargs = msg_tch_cmd.answer.call_args
    assert "📌 📍 Выбор кафедры." in dept_text[0]
    dept_kb = dept_kwargs["reply_markup"]
    dept_cbs = [b.callback_data for row in dept_kb.inline_keyboard for b in row]
    assert "teacher|5|0" in dept_cbs
    assert "teacher|12|0" in dept_cbs

    # Step 2: User clicks Department 12 (callback: 'teacher|12|0')
    call_dept = make_call(user_id=99002, data="teacher|12|0")
    await handle_teacher_menu(call_dept)
    assert call_dept.message.edit_text.call_count == 1
    tch_text, tch_kwargs = call_dept.message.edit_text.call_args
    assert "📌 📍 Выбор преподавателя." in tch_text[0]
    assert "📘 🏛 Кафедра:" in tch_text[0]
    # Ensure no duplicate emojis
    assert "📍 📍" not in tch_text[0]
    assert "🏛 🏛" not in tch_text[0]

    tch_kb = tch_kwargs["reply_markup"]
    tch_cbs = [b.callback_data for row in tch_kb.inline_keyboard for b in row]
    assert "teacherImg|120|0" in tch_cbs
    # Verify teacher from Dept 5 is NOT shown!
    assert not any("50" in cb for cb in tch_cbs)

    # Step 3: Test teacher|0|0 safely redirects to department menu instead of showing empty department ID 0
    call_zero_dept = make_call(user_id=99002, data="teacher|0|0")
    await handle_teacher_menu(call_zero_dept)
    assert call_zero_dept.message.edit_text.call_count == 1
    zero_text, zero_kwargs = call_zero_dept.message.edit_text.call_args
    assert "📌 📍 Выбор кафедры." in zero_text[0]
    assert "teacher|5|0" in [b.callback_data for row in zero_kwargs["reply_markup"].inline_keyboard for b in row]

    # Step 4: Check teacher day text view back button points to teacher's actual department
    from src.bot.handlers.callbacks_router import build_teacher_day_text_view
    cached_tch = {
        "timestamp": 1234567890.0,
        "data": [{"day": "Понедельник", "groups": [{"group": "ИС-21", "subject": "Мат.анализ", "time": "8.30-9.20"}]}],
        "is_from_db": False,
    }
    _, tch_view_kb = await build_teacher_day_text_view(cached_tch, teacher_id=120, day_num=0, ui_lang="ru")
    assert tch_view_kb.inline_keyboard[2][0].callback_data == "teacher|12|0"


@pytest.mark.asyncio
async def test_saved_preferences_direct_schedule_routing(mock_db):
    """Verifies that users with saved group or teacher bypass the catalog directly."""
    grp = Group(id=300, name="ФИЗ-21", language="ru")
    await grp.insert()
    user_with_group = UserModel(userId=99003, group=300, scheduleType="student", language="ru")
    await user_with_group.insert()

    curr_day = get_current_day_number()
    mock_student_schedule = {
        "records": [
            {"dayIndex": curr_day + 1, "time": "8.30-9.20", "slot": 1, "subject": "Физика", "type": "Лекция", "room": "101", "building": "1"}
        ]
    }
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", AsyncMock(return_value=mock_student_schedule)):
        msg = make_msg(user_id=99003, text="🗓 Расписание студента")
        await cmd_group_menu(msg)
        assert msg.answer.call_count == 1
        # Directly rendered schedule for saved group, NOT faculty catalog!
        assert "Физика" in msg.answer.call_args[0][0]

    tch = Teacher(id=400, name="Кузнецов А. А.", department=1)
    await tch.insert()
    user_with_teacher = UserModel(userId=99004, teacher=400, scheduleType="teacher", language="ru")
    await user_with_teacher.insert()

    mock_teacher_schedule = {
        "records": [
            {"dayIndex": curr_day + 1, "time": "8.30-9.20", "slot": 1, "group": "ФИЗ-21", "subject": "Механика", "type": "Прак.зан.", "room": "102", "building": "1"}
        ]
    }
    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_teacher_schedule", AsyncMock(return_value=mock_teacher_schedule)):
        msg_t = make_msg(user_id=99004, text="🗓 Расписание преподавателя")
        await cmd_teacher_menu(msg_t)
        assert msg_t.answer.call_count == 1
        # Directly rendered teacher schedule, NOT department catalog!
        assert "Кузнецов" in msg_t.answer.call_args[0][0]

    # Test Kazakh group language preservation when user interface language is Russian
    grp_kz = Group(id=305, name="ҚАЗ-21", language="kz")
    await grp_kz.insert()
    user_with_kz_group = UserModel(userId=99005, group=305, scheduleType="student", language="ru")
    await user_with_kz_group.insert()

    with patch("src.bot.handlers.callbacks_router.buketov_api_service.get_group_schedule", AsyncMock(return_value=mock_student_schedule)) as mock_api:
        msg_kz = make_msg(user_id=99005, text="🗓 Расписание студента")
        await cmd_group_menu(msg_kz)
        assert msg_kz.answer.call_count == 1
        # Group name passed to API call
        mock_api.assert_called_with("ҚАЗ-21")


@pytest.mark.asyncio
async def test_saved_entity_not_found_in_database(mock_db):
    """Verifies that if saved group/teacher is missing from DB, exact commit 153 error notices are returned."""
    user_missing_grp = UserModel(userId=99010, group=88888, scheduleType="student", language="ru")
    await user_missing_grp.insert()

    msg_g = make_msg(user_id=99010, text="🗓 Расписание студента")
    await cmd_group_menu(msg_g)
    assert msg_g.answer.call_count == 1
    assert msg_g.answer.call_args[0][0] == t("group_not_found", "ru")

    user_missing_tch = UserModel(userId=99011, teacher=77777, scheduleType="teacher", language="kz")
    await user_missing_tch.insert()

    msg_t = make_msg(user_id=99011, text="🗓 Оқытушының кестесі")
    await cmd_teacher_menu(msg_t)
    assert msg_t.answer.call_count == 1
    assert msg_t.answer.call_args[0][0] == t("teacher_not_found", "kz")


@pytest.mark.asyncio
async def test_student_schedule_fallback_back_callbacks(mock_db):
    """Verifies fallback back_callbacks when group has no program or program has no faculty."""
    from src.bot.handlers.callbacks_router import build_student_day_schedule_view

    cached = {
        "timestamp": 1234567890.0,
        "data": [{"day": "Понедельник", "subjects": [{"subject": "Философия", "time": "8.30-9.20"}]}],
        "is_from_db": False,
    }

    # Case 1: Group without program -> fallback 'faculty|0'
    g_no_prog = Group(id=501, name="БЕЗПРОГ-1", language="ru")
    await g_no_prog.insert()
    _, kb1 = await build_student_day_schedule_view(cached, group_id=501, sch_lang="ru", day_num=0, ui_lang="ru")
    assert kb1.inline_keyboard[1][0].callback_data == "faculty|0"

    # Case 2: Group with program, but program has no faculty -> 'group|0|{prog_id}|0'
    p_no_fac = Program(id=55, name="Программа без фак", faculty=None)
    await p_no_fac.insert()
    g_with_prog = Group(id=502, name="СПРОГ-1", program=55, language="ru")
    await g_with_prog.insert()
    _, kb2 = await build_student_day_schedule_view(cached, group_id=502, sch_lang="ru", day_num=0, ui_lang="ru")
    assert kb2.inline_keyboard[1][0].callback_data == "group|0|55|0"


@pytest.mark.asyncio
async def test_language_switch_when_delete_message_fails(mock_db):
    """Verifies handle_set_language does not crash when call.message.delete() raises TelegramBadRequest."""
    from src.bot.handlers.callbacks_router import handle_set_language

    call = make_call(user_id=99020, data="languageIsKz")
    object.__setattr__(call.message, "delete", AsyncMock(side_effect=RuntimeError("Cannot delete message")))

    await handle_set_language(call)
    assert call.message.answer.call_count == 1
    assert call.message.answer.call_args[0][0] == t("welcome_page", "kz")

