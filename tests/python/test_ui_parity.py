"""UI/UX parity verification tests matching Node.js commit 153 exactly.

Tests:
1. Exact reply keyboard buttons for Russian and Kazakh.
2. Exact message texts for /start, /new, /help, /remove.
3. Inline keyboard layouts for language selection and new schedule.
4. Message deletion and welcome page redirection on language switch.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from aiogram.types import CallbackQuery, Chat, Message, ReplyKeyboardRemove, User

from src.bot.handlers.callbacks_router import handle_set_language
from src.bot.handlers.commands_router import (
    handle_help,
    handle_new_schedule,
    handle_remove_keyboard,
    handle_start,
)
from src.bot.keyboards.inline import get_language_keyboard, get_new_schedule_keyboard
from src.bot.keyboards.reply import get_main_reply_keyboard
from src.core.i18n import t


def make_msg(user_id=88100, text="/start", username="ui_tester"):
    u = User(id=user_id, is_bot=False, first_name="Тестер", username=username)
    c = Chat(id=user_id, type="private")
    msg = Message(
        message_id=55,
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


def make_call(user_id=88100, data="languageIsRu"):
    msg = make_msg(user_id=user_id)
    call = CallbackQuery(
        id="call_ui",
        from_user=msg.from_user,
        chat_instance="ui_inst",
        message=msg,
        data=data,
    )
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", msg.bot)
    return call


def test_main_reply_keyboard_ru_and_kz():
    # Russian reply keyboard
    kb_ru = get_main_reply_keyboard(lang="ru")
    assert kb_ru.resize_keyboard is True
    assert kb_ru.one_time_keyboard is True
    assert not getattr(kb_ru, "is_persistent", False)
    assert len(kb_ru.keyboard) == 2
    # Row 1
    assert kb_ru.keyboard[0][0].text == "🗒 Новое расписание"
    assert kb_ru.keyboard[0][1].text == "❓ Помощь "
    # Row 2
    assert kb_ru.keyboard[1][0].text == "🗓 Расписание преподавателя"
    assert kb_ru.keyboard[1][1].text == "🗓 Расписание студента"

    # Kazakh reply keyboard
    kb_kz = get_main_reply_keyboard(lang="kz")
    assert kb_kz.resize_keyboard is True
    assert kb_kz.one_time_keyboard is True
    assert not getattr(kb_kz, "is_persistent", False)
    assert len(kb_kz.keyboard) == 2
    # Row 1
    assert kb_kz.keyboard[0][0].text == "🗒 Жаңа кесте"
    assert kb_kz.keyboard[0][1].text == "❓ Көмек"
    # Row 2
    assert kb_kz.keyboard[1][0].text == "🗓 Оқытушының кестесі"
    assert kb_kz.keyboard[1][1].text == "🗓 Студенттің кестесі"


@pytest.mark.asyncio
async def test_start_command_parity(mock_db):
    msg = make_msg(text="/start")
    await handle_start(msg)

    assert msg.answer.call_count == 1
    text, kwargs = msg.answer.call_args[0][0], msg.answer.call_args[1]

    # Exact bilingual prompt matching Node.js startCommandController.js
    assert text == "🌍 Выберите язык интерфейса. \n🌍 Интерфейстің тіл тандаңыз. "

    kb = kwargs["reply_markup"]
    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[0][0].text == "Русский"
    assert kb.inline_keyboard[0][0].callback_data == "languageIsRu"
    assert kb.inline_keyboard[1][0].text == "Қазақ"
    assert kb.inline_keyboard[1][0].callback_data == "languageIsKz"
    assert kb.inline_keyboard[2][1].text == "❌"
    assert kb.inline_keyboard[2][1].callback_data == "delete"


@pytest.mark.asyncio
async def test_new_schedule_command_parity(mock_db):
    msg = make_msg(text="/new")
    await handle_new_schedule(msg)

    assert msg.answer.call_count == 1
    text, kwargs = msg.answer.call_args[0][0], msg.answer.call_args[1]

    # 3-line format matching Node.js newScheduleCommandController.js
    assert text.startswith("👋 🛰 Чье расписание тебе нужно?")
    assert "<i>💡 P.S. Если тебе лень тыкать по кнопочкам - можешь воспользоваться <b>Поиском</b>.</i>" in text
    assert "📖 Подробнее /search" in text

    kb = kwargs["reply_markup"]
    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[0][0].text == f"🤓 {t('student', 'ru')}"
    assert kb.inline_keyboard[0][0].callback_data == "faculty|0"
    assert kb.inline_keyboard[1][0].text == f"👩‍🏫 {t('teacher', 'ru')}"
    assert kb.inline_keyboard[1][0].callback_data == "department|0"
    assert kb.inline_keyboard[2][1].text == "❌"
    assert kb.inline_keyboard[2][1].callback_data == "delete"


@pytest.mark.asyncio
async def test_language_selection_redirect_to_welcome_page(mock_db):
    call = make_call(data="languageIsRu")
    await handle_set_language(call)

    # 1. Previous message deleted
    assert call.message.delete.call_count == 1
    # 2. Welcome page answered
    assert call.message.answer.call_count == 1
    ans_text, ans_kwargs = call.message.answer.call_args[0][0], call.message.answer.call_args[1]
    assert ans_text == t("welcome_page", "ru")
    # 3. Main reply keyboard attached
    reply_kb = ans_kwargs["reply_markup"]
    assert reply_kb.keyboard[0][0].text == "🗒 Новое расписание"


@pytest.mark.asyncio
async def test_help_and_remove_commands(mock_db):
    # /help
    msg_help = make_msg(text="/help")
    await handle_help(msg_help)
    assert msg_help.answer.call_count == 1
    assert msg_help.answer.call_args[0][0] == t("help_command_content", "ru")

    # /remove
    msg_rm = make_msg(text="/remove")
    await handle_remove_keyboard(msg_rm)
    assert msg_rm.answer.call_count == 1
    assert msg_rm.answer.call_args[0][0] == t("keyboard_deleted", "ru")
    assert isinstance(msg_rm.answer.call_args[1]["reply_markup"], ReplyKeyboardRemove)


@pytest.mark.asyncio
async def test_settings_and_language_select_prompt_parity(mock_db):
    from src.bot.handlers.callbacks_router import handle_language_select_menu
    from src.bot.handlers.commands_router import handle_settings

    # /settings command
    msg_sett = make_msg(text="/settings")
    await handle_settings(msg_sett)
    assert msg_sett.answer.call_count == 1
    assert msg_sett.answer.call_args[0][0] == "🌍 Выберите язык интерфейса. \n🌍 Интерфейстің тіл тандаңыз. "

    # callback 'language_select'
    call_lang = make_call(data="language_select")
    await handle_language_select_menu(call_lang)
    assert call_lang.message.edit_text.call_count == 1
    assert call_lang.message.edit_text.call_args[0][0] == "🌍 Выберите язык интерфейса. \n🌍 Интерфейстің тіл тандаңыз. "


@pytest.mark.asyncio
async def test_silent_refresh_answers_within_cooldown(mock_db):
    """Verifies that 🔄 refresh within cooldown calls call.answer() silently without toast popup."""
    import time
    from src.bot.handlers.callbacks_router import (
        handle_student_schedule_view,
        handle_teacher_table_image,
        handle_teacher_text_schedule,
        student_schedule_cache,
        teacher_table_cache,
        teacher_text_cache,
    )

    now = time.time()
    # 1. Student schedule refresh
    student_schedule_cache["777_ru"] = {
        "timestamp": now - 30,  # 30s ago, well within 10m cooldown
        "data": [{"day": "Понедельник", "subjects": []}],
        "is_from_db": False,
    }
    call_student = make_call(data="refreshschedule|ru|777|0")
    await handle_student_schedule_view(call_student)
    assert call_student.answer.call_count == 1
    assert call_student.answer.call_args[0] == ()
    assert not call_student.answer.call_args[1].get("text")

    # 2. Teacher image table refresh
    teacher_table_cache["888"] = {
        "timestamp": now - 60,  # 1m ago, well within 20m cooldown
        "data": [],
        "is_from_db": False,
    }
    call_img = make_call(data="refreshteacherImg|888|0")
    await handle_teacher_table_image(call_img)
    assert call_img.answer.call_count == 1
    assert call_img.answer.call_args[0] == ()
    assert not call_img.answer.call_args[1].get("text")

    # 3. Teacher text schedule refresh
    teacher_text_cache["888_ru"] = {
        "timestamp": now - 60,  # 1m ago, well within 5m cooldown
        "data": [{"day": "Понедельник", "groups": []}],
        "is_from_db": False,
    }
    call_tch_text = make_call(data="refreshTeacherSchedule|888|0")
    await handle_teacher_text_schedule(call_tch_text)
    assert call_tch_text.answer.call_count == 1
    assert call_tch_text.answer.call_args[0] == ()
    assert not call_tch_text.answer.call_args[1].get("text")


@pytest.mark.asyncio
async def test_student_schedule_view_parity(mock_db):
    """Verifies student schedule header (with group course year), vacation and footer parity."""
    import time
    from src.bot.handlers.callbacks_router import build_student_day_schedule_view
    from src.database.models.group import Group

    # Case 1: Group has age (course year)
    grp1 = Group(id=101, name="ИС-21-1", age=2, language="ru")
    await grp1.insert()

    cached_vacation = {
        "timestamp": time.time(),
        "data": [{"day": "Понедельник", "subjects": []}],
        "is_from_db": False,
    }
    text1, _ = await build_student_day_schedule_view(cached_vacation, group_id=101, sch_lang="ru", day_num=0, ui_lang="ru")
    assert "👥 Группа: ИС-21-1 | 🎓 Курс: 2" in text1
    assert "📆 Расписание на <b>Понедельник</b>:" in text1
    assert "<b>🎉 ВЫХОДНОЙ!</b>" in text1
    assert text1.endswith("schedule.karnu-buketov.edu.kz</a>)\n")

    # Case 2: Group without age fallback
    grp2 = Group(id=102, name="ФИЗ-11", language="ru")
    await grp2.insert()
    text2, _ = await build_student_day_schedule_view(cached_vacation, group_id=102, sch_lang="ru", day_num=0, ui_lang="ru")
    assert "👥 <b>ФИЗ-11</b>\n📆 Расписание на <b>Понедельник</b>:" in text2


@pytest.mark.asyncio
async def test_teacher_text_schedule_view_parity(mock_db):
    """Verifies teacher text schedule transformGroupString, subject badge, header title, and footer."""
    import time
    from src.bot.handlers.callbacks_router import (
        build_teacher_day_text_view,
        format_subject_with_lesson_type,
        transform_group_string,
    )
    from src.database.models.teacher import Teacher

    # 1. Test transform_group_string
    assert transform_group_string("ИС-21-1 (301/1)") == "ИС-21-1 (Ауд. 301 | 1 корпус)"
    assert transform_group_string("ИС-21-1 (301/1) ИТ-21-2 (302/2)") == "ИС-21-1 (Ауд. 301 | 1 корпус)\nИТ-21-2 (Ауд. 302 | 2 корпус)"
    assert transform_group_string("ИС-21-1") == "ИС-21-1"

    # 2. Test format_subject_with_lesson_type
    assert format_subject_with_lesson_type("Базы данных", "Лекция") == "Базы данных /Лекция/"
    assert format_subject_with_lesson_type("Математика (лек)", None) == "Математика /Лекция/"
    assert format_subject_with_lesson_type("Физика (пр)", None) == "Физика /Прак.зан./"
    assert format_subject_with_lesson_type("Химия (лаб)", None) == "Химия /Лаб.раб./"
    assert format_subject_with_lesson_type("Информатика (сроп)", None) == "Информатика /СРОП/"

    # 3. Test teacher text schedule formatting with head of department
    tch_head = Teacher(id=9999, name="Тестовый Т. Т.", is_head=True, department=5)
    await tch_head.insert()

    cached_schedule = {
        "timestamp": time.time(),
        "data": [
            {
                "day": "Понедельник",
                "groups": [
                    {
                        "time": "8.30-9.20",
                        "group": "ИС-21-1 (301/1)",
                        "subject": "Базы данных",
                        "lessonType": "Лекция",
                    }
                ],
            }
        ],
        "is_from_db": False,
    }
    text_ru, _ = await build_teacher_day_text_view(cached_schedule, teacher_id=9999, day_num=0, ui_lang="ru")
    assert "👥 <b>Тестовый Т. Т.</b> (Заведующий кафедрой)" in text_ru
    assert "📆 Расписание на <b>Понедельник</b>:" in text_ru
    assert "⌚️ 8.30-9.20\n👥 ИС-21-1 (Ауд. 301 | 1 корпус)\n📖 <i>Базы данных /Лекция/</i>" in text_ru
    assert "🕰 <i><b>" in text_ru
    assert text_ru.endswith("schedule.karnu-buketov.edu.kz</a>)\n")

    # 4. Teacher vacation format
    cached_empty = {
        "timestamp": time.time(),
        "data": [{"day": "Понедельник", "groups": []}],
        "is_from_db": False,
    }
    vac_ru, _ = await build_teacher_day_text_view(cached_empty, teacher_id=9999, day_num=0, ui_lang="ru")
    assert "🥳 <b>🎉 ВЫХОДНОЙ!</b>\n\n" in vac_ru


@pytest.mark.asyncio
async def test_day_name_localization_with_ui_language(mock_db):
    """Verifies that day name in header adheres to user's ui_lang matching Node.js commit 25601dc."""
    import time
    from src.bot.handlers.callbacks_router import (
        build_student_day_schedule_view,
        build_teacher_day_text_view,
    )
    from src.database.models.group import Group
    from src.database.models.teacher import Teacher

    grp = Group(id=301, name="ИС-21-1", age=3, language="ru")
    await grp.insert()
    tch = Teacher(id=3001, name="Ахметов Б. Т.", department=1)
    await tch.insert()

    # Cached data has Russian day names, but user has Kazakh interface
    cached_ru_data = {
        "timestamp": time.time(),
        "data": [{"day": "Понедельник", "subjects": []}],
        "is_from_db": False,
    }
    # Student view in Kazakh
    text_kz, _ = await build_student_day_schedule_view(cached_ru_data, group_id=301, sch_lang="ru", day_num=0, ui_lang="kz")
    assert "👥 ИС-21-1 тобы | 🎓 3 курсы" in text_kz
    assert "<b>Дүйсенбі</b> кестесі:" in text_kz
    assert "Понедельник" not in text_kz
    assert "<b>🎉 ДЕМАЛЫС!</b>" in text_kz

    # Teacher view in Kazakh
    cached_teacher_ru = {
        "timestamp": time.time(),
        "data": [{"day": "Понедельник", "groups": []}],
        "is_from_db": False,
    }
    tch_text_kz, _ = await build_teacher_day_text_view(cached_teacher_ru, teacher_id=3001, day_num=0, ui_lang="kz")
    assert "<b>Дүйсенбі</b> кестесі:" in tch_text_kz
    assert "Понедельник" not in tch_text_kz
    assert "🥳 <b>🎉 ДЕМАЛЫС!</b>" in tch_text_kz


@pytest.mark.asyncio
async def test_group_name_fallback_for_course_year(mock_db):
    """Verifies group_name fallback finds group in DB and extracts course year when group_id is 0."""
    import time
    from src.bot.handlers.callbacks_router import build_student_day_schedule_view
    from src.database.models.group import Group

    grp = Group(id=405, name="ЮР-22-2", age=4, language="ru")
    await grp.insert()

    cached = {
        "timestamp": time.time(),
        "data": [{"day": "Понедельник", "subjects": []}],
        "is_from_db": False,
    }
    text, _ = await build_student_day_schedule_view(cached, group_id=0, sch_lang="ru", day_num=0, ui_lang="ru", group_name="ЮР-22-2")
    assert "👥 Группа: ЮР-22-2 | 🎓 Курс: 4" in text


@pytest.mark.asyncio
async def test_teacher_table_image_caption_parity(mock_db):
    """Verifies that teacher table photo caption uses localized teacher_grid_caption in RU and KZ."""
    import time
    from src.bot.handlers.callbacks_router import handle_teacher_table_image, teacher_table_cache
    from src.database.models.teacher import Teacher
    from src.services.user_service import user_service

    tch = Teacher(id=7701, name="Калиева А. М.", department=10)
    await tch.insert()

    now = time.time()
    teacher_table_cache["7701"] = {
        "timestamp": now - 30,  # inside 20m cooldown
        "data": [],
        "is_from_db": False,
    }

    # Case 1: Russian user
    await user_service.set_user_language(88100, "ru")
    call_ru = make_call(user_id=88100, data="refreshteacherImg|7701|0")
    object.__setattr__(call_ru.message, "photo", [AsyncMock()])
    object.__setattr__(call_ru.message, "edit_caption", AsyncMock())
    await handle_teacher_table_image(call_ru)

    assert call_ru.message.edit_caption.call_count == 1
    caption_ru = call_ru.message.edit_caption.call_args[1]["caption"]
    assert "👥 <b>Калиева А. М.</b>\n📊 Таблица занятости на неделю" in caption_ru
    assert "Загруженность:" not in caption_ru
    assert "🕒 <i><b>" in caption_ru

    # Case 2: Kazakh user
    await user_service.set_user_language(88100, "kz")
    call_kz = make_call(user_id=88100, data="refreshteacherImg|7701|0")
    object.__setattr__(call_kz.message, "photo", [AsyncMock()])
    object.__setattr__(call_kz.message, "edit_caption", AsyncMock())
    await handle_teacher_table_image(call_kz)

    assert call_kz.message.edit_caption.call_count == 1
    caption_kz = call_kz.message.edit_caption.call_args[1]["caption"]
    assert "👥 <b>Калиева А. М.</b>\n📊 Апталық сабақ кестесі" in caption_kz
    assert "Загруженность:" not in caption_kz
    assert "🕒 <i><b>" in caption_kz


def test_export_script_almaty_timezone_and_retroactive_callback_parsing():
    from scratch.export_database_to_drive import (
        ALMATY_TZ,
        format_date,
        json_serializer,
        deduplicate_actions,
    )

    # 1. format_date converts UTC datetime to Asia/Almaty (UTC+5)
    utc_dt = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    formatted = format_date(utc_dt)
    assert formatted == "01.09.2026 15:00:00"

    # ISO string with 'Z'
    formatted_iso = format_date("2026-09-01T10:00:00Z")
    assert formatted_iso == "01.09.2026 15:00:00"

    # 2. json_serializer explicitly includes +05:00 offset
    serialized = json_serializer(utc_dt)
    assert serialized == "2026-09-01T15:00:00+05:00"

    # 3. deduplicate_actions deduplicates by userId+timeKey+action+text
    raw_actions = [
        {"userId": 1, "createdAt": "2026-09-01T10:00:00Z", "action": "button", "text": "group|10|61|0"},
        {"userId": 1, "createdAt": "2026-09-01T10:00:00Z", "action": "button", "text": "group|10|61|0"},
        {"userId": 1, "createdAt": "2026-09-01T10:05:00Z", "action": "button", "text": "schedule|рус|6474|0"},
    ]
    actions, dupes = deduplicate_actions(raw_actions)
    assert len(actions) == 2
    assert dupes == 1


