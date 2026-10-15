"""Bot command handlers router.

Handles standard commands (/start, /help, /profile, /settings, /new, /schedule,
/teacher, /group, /search, /remove) as well as human-friendly button text aliases.
"""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, ReplyKeyboardRemove

from src.bot.keyboards.inline import (
    get_faculties_keyboard,
    get_language_keyboard,
    get_new_schedule_keyboard,
)
from src.core.i18n import t
from src.services.user_service import user_service

commands_router = Router(name="commands_router")


@commands_router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Handles /start command with user registration and greeting."""
    user = message.from_user
    if not user:
        return

    # Auto-register user
    await user_service.get_or_create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    lang = await user_service.get_user_language(user.id)
    greeting = (
        f"👋 <b>Сәлем, {user.first_name or 'дос'}!</b>\n"
        f"Бөкетов атындағы ҚарУ ресми сабақ кестесі ботына қош келдіңіз!\n\n"
        f"👋 <b>Привет, {user.first_name or 'друг'}!</b>\n"
        f"Добро пожаловать в официальный бот расписания КарУ им. Букетова!\n\n"
        f"<i>{t('new_schedule_menu_content', lang)}</i>"
    )

    await message.answer(
        greeting,
        reply_markup=get_new_schedule_keyboard(lang),
        parse_mode="HTML",
    )


@commands_router.message(Command("help"))
@commands_router.message(F.text.regexp(r"^(?:[💡❓⚠️\u26A0\uFE0F\s])*(?:Помощь|Көмек|Возникла проблема|Қате|Мәселе|произошла ошибка)", flags=2))
async def handle_help(message: Message) -> None:
    """Handles /help command and help text triggers."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    help_text = t("help_command_content", lang)
    compare_link = t("official_site_compare", lang)
    feedback_info = t("for_help", lang)

    full_text = f"{help_text}\n\n{compare_link}\n{feedback_info}"
    await message.answer(full_text, parse_mode="HTML", disable_web_page_preview=True)


@commands_router.message(Command("profile"))
@commands_router.message(F.text.regexp(r"^(?:👤\s*)?(?:профиль|менің профилім)", flags=2))
async def handle_profile(message: Message) -> None:
    """Handles /profile command displaying user details and preferences."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)

    user_doc = await user_service.get_or_create_user(user_id)
    group_name = "Не выбрана"
    teacher_name = "Не выбран"

    if user_doc and user_doc.group:
        try:
            from src.database.models.group import Group

            grp = await Group.find_one({"id": user_doc.group})
            if grp:
                group_name = grp.name
        except Exception:
            group_name = str(user_doc.group)

    if user_doc and user_doc.teacher:
        try:
            from src.database.models.teacher import Teacher

            tch = await Teacher.find_one({"id": user_doc.teacher})
            if tch:
                teacher_name = tch.name
        except Exception:
            teacher_name = str(user_doc.teacher)

    lang_display = "🇷🇺 Русский" if lang == "ru" else "🇰🇿 Қазақша"
    profile_text = (
        f"👤 <b>Профиль пользователя:</b>\n\n"
        f"• <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"• <b>Имя:</b> {message.from_user.full_name if message.from_user else '—'}\n"
        f"• <b>Язык:</b> {lang_display}\n"
        f"• <b>Группа:</b> {group_name}\n"
        f"• <b>Преподаватель:</b> {teacher_name}\n\n"
        f"<i>Для выбора или смены расписания нажмите /new</i>"
    )

    await message.answer(profile_text, parse_mode="HTML")


@commands_router.message(Command("schedule"))
@commands_router.message(F.text.regexp(r"^расписание", flags=2))
async def handle_schedule_command(message: Message) -> None:
    """Handles /schedule and 'расписание' command, opening saved group or teacher schedule."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    user_doc = await user_service.get_or_create_user(user_id)

    from src.bot.handlers.callbacks_router import (
        get_current_day_number,
        render_student_day_schedule_message,
        render_teacher_day_schedule_message,
    )

    curr_day = get_current_day_number()

    if user_doc and user_doc.schedule_type == "student" and user_doc.group:
        sch_lang = "kz" if lang == "kz" else "ru"
        await render_student_day_schedule_message(
            target_msg=message,
            group_id=user_doc.group,
            sch_lang=sch_lang,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
        )
        return

    if user_doc and user_doc.schedule_type == "teacher" and user_doc.teacher:
        await render_teacher_day_schedule_message(
            target_msg=message,
            teacher_id=user_doc.teacher,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
        )
        return

    # No saved preference -> redirect to /new menu
    await handle_new_schedule(message)


@commands_router.message(Command("settings"))
async def handle_settings(message: Message) -> None:
    """Handles /settings command for language switching."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)

    prompt = "Тілді таңдаңыз / Выберите язык интерфейса:"
    await message.answer(prompt, reply_markup=get_language_keyboard())


@commands_router.message(Command("new"))
@commands_router.message(F.text.regexp(r"^🗒\s*(?:Новое расписание|Жаңа кесте)", flags=2))
async def handle_new_schedule(message: Message) -> None:
    """Handles /new command opening the root faculties catalog."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)

    try:
        from src.database.models.faculty import Faculty

        faculties = await Faculty.find().sort("id").to_list()
    except Exception:
        faculties = []

    markup = get_faculties_keyboard(faculties, page=0, lang=lang)
    page_text = t("page_text", lang, page=1, pageCount=max(1, (len(faculties) + 9) // 10))
    msg_text = f"📍 {t('faculty_pick', lang)}\n{page_text}"

    await message.answer(msg_text, reply_markup=markup, parse_mode="HTML")


@commands_router.message(Command("teacher"))
@commands_router.message(F.text.regexp(r"^(?:🗓\s*(?:Расписание преподавателя|Оқытушының кестесі)|[Пп]реподаватель|[Оо]қытушы)$"))
async def handle_teacher_menu(message: Message) -> None:
    """Handles /teacher command opening the department catalog."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)

    try:
        from src.database.models.department import Department

        departments = await Department.find().sort("name").to_list()
    except Exception:
        departments = []

    from src.bot.keyboards.inline import get_departments_keyboard

    markup = get_departments_keyboard(departments, page=0, lang=lang)
    page_text = t("page_text", lang, page=1, pageCount=max(1, (len(departments) + 9) // 10))
    msg_text = f"📍 {t('department_pick', lang)}\n{page_text}"

    await message.answer(msg_text, reply_markup=markup, parse_mode="HTML")


@commands_router.message(Command("group"))
@commands_router.message(F.text.regexp(r"^(?:🗓\s*(?:Расписание студента|Студенттің кестесі)|[Гг]руппа|[Тт]обы)$"))
async def handle_group_menu(message: Message) -> None:
    """Handles /group command opening the faculty catalog for students."""
    await handle_new_schedule(message)


# Search prefix triggers: 'Г <group>', 'Т <group>', 'Г', 'Т', 'Группа <group>', 'Тобы <group>'
@commands_router.message(F.text.regexp(r"^(?:[ГгТт]|[Гг]руппа|[Тт]обы)(?:\s+(.*))?$"))
async def handle_group_search_prefix(message: Message) -> None:
    """Handles group search shortcuts with validation."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    text = (message.text or "").strip()
    match = re.match(r"^(?:[ГгТт]|[Гг]руппа|[Тт]обы)(?:\s+(.*))?$", text, re.IGNORECASE)
    query = (match.group(1) or "").strip() if match else ""

    if not query or len(query) < 2:
        await message.answer(t("group_search_validation_error", lang), parse_mode="HTML")
        return

    from src.bot.handlers.callbacks_router import (
        get_current_day_number,
        render_student_day_schedule_message,
    )
    from src.bot.keyboards.inline import get_search_results_keyboard
    from src.services.smart_search import smart_search_service

    groups = await smart_search_service.get_all_groups()
    matched = smart_search_service.find_groups(query, groups)

    if not matched:
        await message.answer(t("search_bad_result", lang, searchQuery=query), parse_mode="HTML")
        return

    if len(matched) == 1:
        g = matched[0]
        g_id = g.get("id") or g.get("group_id")
        g_lang = g.get("language") or ("kz" if lang == "kz" else "ru")
        curr_day = get_current_day_number()
        await render_student_day_schedule_message(
            target_msg=message,
            group_id=g_id,
            sch_lang=g_lang,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
            group_name=g.get("name"),
        )
        return

    markup = get_search_results_keyboard(groups=matched, lang=lang)
    await message.answer(
        f"✨ По вашему запросу: <b>{query}</b> найдено групп: <b>{len(matched)}</b>\n\n"
        "Выберите нужную группу из списка:",
        reply_markup=markup,
        parse_mode="HTML",
    )


# Search prefix triggers: 'П <teacher>', 'О <teacher>', 'П', 'О', 'Преподаватель <teacher>', 'Оқытушы <teacher>'
@commands_router.message(F.text.regexp(r"^(?:[ПпОо]|[Пп]реподаватель|[Оо]қытушы)(?:\s+(.*))?$"))
async def handle_teacher_search_prefix(message: Message) -> None:
    """Handles teacher search shortcuts with validation."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    text = (message.text or "").strip()
    match = re.match(r"^(?:[ПпОо]|[Пп]реподаватель|[Оо]қытушы)(?:\s+(.*))?$", text, re.IGNORECASE)
    query = (match.group(1) or "").strip() if match else ""

    if not query or len(query) < 2:
        await message.answer(t("teacher_search_validation_error", lang), parse_mode="HTML")
        return

    from src.bot.handlers.callbacks_router import (
        get_current_day_number,
        render_teacher_day_schedule_message,
    )
    from src.bot.keyboards.inline import get_search_results_keyboard
    from src.services.smart_search import smart_search_service

    teachers = await smart_search_service.get_all_teachers()
    matched = smart_search_service.find_teachers(query, teachers)

    if not matched:
        await message.answer(t("search_bad_result", lang, searchQuery=query), parse_mode="HTML")
        return

    if len(matched) == 1:
        tch = matched[0]
        t_id = tch.get("id") or tch.get("teacher_id")
        curr_day = get_current_day_number()
        await render_teacher_day_schedule_message(
            target_msg=message,
            teacher_id=t_id,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
            teacher_name=tch.get("fullName") or tch.get("name"),
        )
        return

    markup = get_search_results_keyboard(teachers=matched, lang=lang)
    await message.answer(
        f"✨ По вашему запросу: <b>{query}</b> найдено преподавателей: <b>{len(matched)}</b>\n\n"
        "Выберите преподавателя из списка:",
        reply_markup=markup,
        parse_mode="HTML",
    )


@commands_router.message(Command("search"))
@commands_router.message(F.text.regexp(r"^Поиск", flags=2))
async def handle_search_help(message: Message) -> None:
    """Handles /search command explaining smart text search."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    await message.answer(t("search_command_content", lang), parse_mode="HTML")


@commands_router.message(Command("remove"))
async def handle_remove_keyboard(message: Message) -> None:
    """Removes reply keyboard."""
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    await message.answer(
        t("keyboard_deleted", lang),
        reply_markup=ReplyKeyboardRemove(),
    )


__all__ = [
    "commands_router",
    "handle_group_menu",
    "handle_group_search_prefix",
    "handle_help",
    "handle_new_schedule",
    "handle_profile",
    "handle_remove_keyboard",
    "handle_schedule_command",
    "handle_search_help",
    "handle_settings",
    "handle_start",
    "handle_teacher_menu",
    "handle_teacher_search_prefix",
]
