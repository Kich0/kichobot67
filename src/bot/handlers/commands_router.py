"""Bot command handlers router.

Handles standard commands (/start, /help, /profile, /settings, /new, /schedule,
/teacher, /group, /search, /remove) as well as human-friendly button text aliases.
"""

from __future__ import annotations

from html import escape as escape_html
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


async def remove_reply_keyboard_safely(message: Message) -> None:
    """Removes reply keyboard from user client by briefly sending ReplyKeyboardRemove."""
    try:
        bot = getattr(message, "bot", None) or getattr(message, "_bot", None)
        chat_id = message.chat.id if message.chat else None
        if bot and hasattr(bot, "send_message") and chat_id:
            tmp = await bot.send_message(chat_id=chat_id, text="⏳", reply_markup=ReplyKeyboardRemove())
            if hasattr(tmp, "delete"):
                await tmp.delete()
        else:
            tmp = await message.answer("⏳", reply_markup=ReplyKeyboardRemove())
            if hasattr(tmp, "delete"):
                await tmp.delete()
    except Exception:
        pass


@commands_router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Handles /start command: sends bilingual language selector with inline keyboard."""
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

    msg_text = (
        "🌍 Выберите язык интерфейса. \n"
        "🌍 Интерфейстің тіл тандаңыз. "
    )

    await message.answer(
        msg_text,
        reply_markup=get_language_keyboard(),
        parse_mode="HTML",
    )


@commands_router.message(Command("help"))
@commands_router.message(F.text.regexp(r"^(?:[💡❓⚠️\u26A0\uFE0F\s])*(?:Помощь|Көмек|Возникла проблема|Қате|Мәселе|произошла ошибка)", flags=2))
async def handle_help(message: Message) -> None:
    """Handles /help command and help text triggers."""
    text = (message.text or "").strip()
    is_private = not getattr(message, "chat", None) or message.chat.type == "private"
    if not is_private and not text.startswith("/"):
        return
    try:
        await message.delete()
    except Exception:
        pass
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    await message.answer(
        t("help_command_content", lang),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=ReplyKeyboardRemove(),
    )


@commands_router.message(Command("profile"))
@commands_router.message(F.text.regexp(r"^(?:👤\s*)?(?:профиль|менің профилім)", flags=2))
async def handle_profile(message: Message) -> None:
    """Handles /profile command displaying user details and preferences."""
    text = (message.text or "").strip()
    is_private = not getattr(message, "chat", None) or message.chat.type == "private"
    if not is_private and not text.startswith("/"):
        return
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)

    user_doc = await user_service.get_or_create_user(user_id)
    group_name = t("not_selected_female", lang)
    teacher_name = t("not_selected_male", lang)

    if user_doc and user_doc.group:
        try:
            from src.database.models.group import Group

            num_g = int(user_doc.group) if str(user_doc.group).isdigit() else None
            grp = None
            if num_g is not None:
                grp = await Group.find_one({"id": num_g}) or await Group.find_one({"groupId": num_g})
            if not grp:
                grp = await Group.find_one({"name": str(user_doc.group)})
            if grp:
                group_name = grp.name
        except Exception:
            group_name = str(user_doc.group)

    if user_doc and user_doc.teacher:
        try:
            from src.database.models.teacher import Teacher

            num_t = int(user_doc.teacher) if str(user_doc.teacher).isdigit() else user_doc.teacher
            tch = (
                await Teacher.find_one({"id": num_t})
                or await Teacher.find_one({"teacher_id": num_t})
                or await Teacher.find_one({"name": str(user_doc.teacher)})
                or await Teacher.find_one({"fullName": str(user_doc.teacher)})
            )
            if tch:
                teacher_name = tch.name
        except Exception:
            teacher_name = str(user_doc.teacher)

    lang_display = "🇷🇺 Русский" if lang == "ru" else "🇰🇿 Қазақша"
    user_name = escape_html(message.from_user.full_name) if message.from_user else "—"
    profile_text = t(
        "user_profile_card",
        lang,
        userId=user_id,
        userName=user_name,
        langDisplay=lang_display,
        groupName=escape_html(group_name),
        teacherName=escape_html(teacher_name),
    )

    await message.answer(profile_text, parse_mode="HTML")


@commands_router.message(Command("schedule"))
@commands_router.message(F.text.regexp(r"^(?:расписание|кесте|кестесі)(?:\b|\s|$)", flags=2))
@commands_router.message(F.text.regexp(r"^@(?:\w+bot|kicho1bot)\s+(?:расписание|кесте|кестесі)(?:\b|\s|$)", flags=2))
@commands_router.message(F.text.regexp(r"^@(?:\w+bot|kicho1bot)$", flags=2))
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
            viewer_user_id=user_id,
        )
        return

    if user_doc and user_doc.schedule_type == "teacher" and user_doc.teacher:
        await render_teacher_day_schedule_message(
            target_msg=message,
            teacher_id=user_doc.teacher,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
            viewer_user_id=user_id,
        )
        return

    # No saved preference -> redirect to /new menu
    await handle_new_schedule(message)


@commands_router.message(Command("settings"))
async def handle_settings(message: Message) -> None:
    """Handles /settings command for language switching."""
    prompt = (
        "🌍 Выберите язык интерфейса. \n"
        "🌍 Интерфейстің тіл тандаңыз. "
    )
    await message.answer(prompt, reply_markup=get_language_keyboard(), parse_mode="HTML")


@commands_router.message(Command("new"))
@commands_router.message(F.text.regexp(r"^🗒\s*(?:Новое расписание|Жаңа кесте)", flags=2))
async def handle_new_schedule(message: Message) -> None:
    """Handles /new and '🗒 Новое расписание' / '🗒 Жаңа кесте' command, opening student vs teacher picker."""
    text = (message.text or "").strip()
    is_private = not getattr(message, "chat", None) or message.chat.type == "private"
    if not is_private and not text.startswith("/"):
        return
    is_reply_button = bool(message.text and not message.text.startswith("/"))
    try:
        await message.delete()
    except Exception:
        pass
    if is_reply_button:
        await remove_reply_keyboard_safely(message)

    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)

    msg_text = (
        f"👋 {t('new_schedule_menu_content', lang)}\n"
        f"<i>{t('new_schedule_menu_hint', lang)}</i>\n"
        f"{t('new_schedule_menu_hint2', lang)}"
    )

    await message.answer(
        msg_text,
        reply_markup=get_new_schedule_keyboard(lang),
        parse_mode="HTML",
    )


@commands_router.message(Command("teacher"))
@commands_router.message(F.text.regexp(r"^(?:🗓\s*(?:Расписание преподавателя|Оқытушының кестесі)|[Пп]реподаватель|[Оо]қытушы)$"))
async def handle_teacher_menu(message: Message) -> None:
    """Handles /teacher and '🗓 Расписание преподавателя' / '🗓 Оқытушының кестесі'."""
    text = (message.text or "").strip()
    is_private = not getattr(message, "chat", None) or message.chat.type == "private"
    if not is_private and not text.startswith("/"):
        return
    is_reply_button = bool(message.text and not message.text.startswith("/"))
    try:
        await message.delete()
    except Exception:
        pass
    if is_reply_button:
        await remove_reply_keyboard_safely(message)

    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    user_doc = await user_service.get_or_create_user(user_id)

    if user_doc and user_doc.teacher:
        from src.bot.handlers.callbacks_router import (
            get_current_day_number,
            render_teacher_day_schedule_message,
        )
        from src.database.models.teacher import Teacher
        from src.services.teacher_directory import teacher_directory_service

        tch_name = None
        t_doc = None
        try:
            num_t_id = int(user_doc.teacher) if str(user_doc.teacher).isdigit() else user_doc.teacher
            if isinstance(num_t_id, int):
                t_doc = await Teacher.find_one(Teacher.teacher_id == num_t_id) or await Teacher.find_one({"id": num_t_id})
            else:
                t_doc = await Teacher.find_one(Teacher.name == str(num_t_id)) or await Teacher.find_one({"name": str(num_t_id)})
            if t_doc:
                enriched = teacher_directory_service.enrich(t_doc.model_dump(by_alias=True))
                tch_name = enriched.get("fullName") or t_doc.name
        except Exception:
            pass

        if not t_doc and user_doc.teacher:
            matched_dir = teacher_directory_service.find_match(str(user_doc.teacher))
            if matched_dir:
                enriched = teacher_directory_service.enrich(matched_dir)
                tch_name = enriched.get("fullName") or enriched.get("name") or str(user_doc.teacher)

        if not t_doc and not tch_name:
            await message.answer(t("teacher_not_found", lang), parse_mode="HTML")
            return

        curr_day = get_current_day_number()
        await render_teacher_day_schedule_message(
            target_msg=message,
            teacher_id=user_doc.teacher,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
            teacher_name=tch_name,
        )
        return

    try:
        from src.database.models.department import Department

        departments = await Department.find().sort("name").to_list()
    except Exception:
        departments = []

    from src.bot.keyboards.inline import get_departments_keyboard

    markup = get_departments_keyboard(departments, page=0, lang=lang)
    page_text = t("page_text", lang, page=1, pageCount=max(1, (len(departments) + 9) // 10))
    current_menu_text = f"📌 {t('department_pick', lang)}"
    menu_hint = t("department_pick_hint", lang)
    msg_text = f"{current_menu_text}\n{menu_hint}\n{page_text}"

    await message.answer(msg_text, reply_markup=markup, parse_mode="HTML")


@commands_router.message(Command("group"))
@commands_router.message(F.text.regexp(r"^(?:🗓\s*(?:Расписание студента|Студенттің кестесі)|[Гг]руппа|[Тт]обы)$"))
async def handle_group_menu(message: Message) -> None:
    """Handles /group and '🗓 Расписание студента' / '🗓 Студенттің кестесі'."""
    text = (message.text or "").strip()
    is_private = not getattr(message, "chat", None) or message.chat.type == "private"
    if not is_private and not text.startswith("/"):
        return
    is_reply_button = bool(message.text and not message.text.startswith("/"))
    try:
        await message.delete()
    except Exception:
        pass
    if is_reply_button:
        await remove_reply_keyboard_safely(message)

    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    user_doc = await user_service.get_or_create_user(user_id)

    if user_doc and user_doc.group:
        from src.bot.handlers.callbacks_router import (
            get_current_day_number,
            render_student_day_schedule_message,
        )
        from src.database.models.group import Group

        grp_name = None
        grp_lang = None
        g_doc = None
        try:
            num_g_id = int(user_doc.group) if str(user_doc.group).isdigit() else user_doc.group
            g_doc = await Group.find_one(Group.group_id == num_g_id) or await Group.find_one({"id": num_g_id})
            if g_doc:
                grp_name = g_doc.name
                grp_lang = g_doc.language
        except Exception:
            pass

        if not g_doc:
            await message.answer(t("group_not_found", lang), parse_mode="HTML")
            return

        sch_lang = grp_lang or ("kz" if lang == "kz" else "ru")
        curr_day = get_current_day_number()
        await render_student_day_schedule_message(
            target_msg=message,
            group_id=user_doc.group,
            sch_lang=sch_lang,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
            group_name=grp_name,
        )
        return

    try:
        from src.database.models.faculty import Faculty

        faculties = await Faculty.find().sort("id").to_list()
    except Exception:
        faculties = []

    markup = get_faculties_keyboard(faculties, page=0, lang=lang)
    page_text = t("page_text", lang, page=1, pageCount=max(1, (len(faculties) + 9) // 10))
    msg_text = f"{t('faculty_pick', lang)} \n{page_text}"

    await message.answer(msg_text, reply_markup=markup, parse_mode="HTML")


# Search prefix triggers: 'Г <group>', 'Т <group>', 'Г', 'Т', 'Группа <group>', 'Тобы <group>'
@commands_router.message(F.text.regexp(r"^(?:[ГгТт]|[Гг]руппа|[Тт]обы)(?:\s+(.*))?$"))
async def handle_group_search_prefix(message: Message) -> None:
    """Handles group search shortcuts with validation."""
    is_private = not getattr(message, "chat", None) or message.chat.type == "private"
    if not is_private:
        return
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    text = (message.text or "").strip()
    match = re.match(r"^(?:[ГгТт]|[Гг]руппа|[Тт]обы)(?:\s+(.*))?$", text, re.IGNORECASE)
    query = (match.group(1) or "").strip() if match else ""

    if not query or not re.search(r"[a-zA-Zа-яА-ЯёЁәіңғүұқөһӘІҢҒҮҰҚӨҺ0-9]", query):
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

    curr_day = get_current_day_number()
    if len(matched) == 1:
        g = matched[0]
        g_id = g.get("id") or g.get("group_id")
        g_lang = g.get("language") or ("kz" if lang == "kz" else "ru")
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

    markup = get_search_results_keyboard(groups=matched, lang=lang, current_day=curr_day)
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
    is_private = not getattr(message, "chat", None) or message.chat.type == "private"
    if not is_private:
        return
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    text = (message.text or "").strip()
    match = re.match(r"^(?:[ПпОо]|[Пп]реподаватель|[Оо]қытушы)(?:\s+(.*))?$", text, re.IGNORECASE)
    query = (match.group(1) or "").strip() if match else ""

    if not query or not re.search(r"[a-zA-Zа-яА-ЯёЁәіңғүұқөһӘІҢҒҮҰҚӨҺ0-9]", query):
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

    curr_day = get_current_day_number()
    if len(matched) == 1:
        tch = matched[0]
        t_name = tch.get("fullName") or tch.get("name") or ""
        t_id = tch.get("id") or tch.get("teacher_id") or tch.get("name") or t_name
        await render_teacher_day_schedule_message(
            target_msg=message,
            teacher_id=t_id,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
            teacher_name=t_name,
        )
        return

    markup = get_search_results_keyboard(teachers=matched, lang=lang, current_day=curr_day)
    await message.answer(
        f"✨ По вашему запросу: <b>{query}</b> найдено преподавателей: <b>{len(matched)}</b>\n\n"
        "Выберите преподавателя из списка:",
        reply_markup=markup,
        parse_mode="HTML",
    )


@commands_router.message(Command("search"))
@commands_router.message(F.text.regexp(r"^Поиск", flags=2))
async def handle_search_help(message: Message) -> None:
    """Handles /search command explaining smart text search or running search query."""
    text = (message.text or "").strip()
    is_private = not getattr(message, "chat", None) or message.chat.type == "private"
    if not is_private and not text.startswith("/"):
        return

    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)

    parts = text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    if query:
        from src.bot.handlers.search_router import handle_text_search

        msg = message.model_copy(update={"text": query})
        await handle_text_search(msg)
        return
    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    await message.answer(t("search_command_content", lang), parse_mode="HTML")


@commands_router.message(Command("remove"))
async def handle_remove_keyboard(message: Message) -> None:
    """Removes reply keyboard."""
    try:
        await message.delete()
    except Exception:
        pass
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
    "remove_reply_keyboard_safely",
]
