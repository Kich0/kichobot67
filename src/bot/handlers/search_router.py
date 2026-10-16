"""Smart Search message router.

Routes free-text messages through SmartSearchService using RapidFuzz
and phonetic Cyrillic/Kazakh normalization for instantaneous schedule lookup,
disambiguation menus, and typo-tolerant suggestions.
"""

from __future__ import annotations

import re
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards.inline import (
    get_fuzzy_suggestions_keyboard,
    get_schedule_pagination_keyboard,
    get_search_results_keyboard,
    get_teacher_table_keyboard,
)
from src.bot.middlewares.user_action_logger import (
    register_group_name,
    register_teacher_name,
    user_action_logging_middleware,
)
from src.core.i18n import t
from src.core.logging import get_logger
from src.services.smart_search import (
    SearchResultType,
    SmartSearchResult,
    smart_search_service,
)
from src.services.teacher_directory import is_kazakh_text
from src.services.user_service import user_service

logger = get_logger("search_router")
search_router = Router(name="search_router")

# Prefixes that represent commands and should be ignored by free-text search
COMMAND_PREFIXES = (
    "/",
    "🗒",
    "🗓",
    "💡",
    "❓",
    "⚠️",
)


@search_router.message(F.text)
async def handle_text_search(message: Message) -> None:
    """Processes free-text message as a smart search query."""
    text = (message.text or "").strip()
    if not text:
        return

    # Skip commands and button labels
    if any(text.startswith(p) for p in COMMAND_PREFIXES):
        return

    user_id = message.from_user.id if message.from_user else 0
    lang = await user_service.get_user_language(user_id)
    if is_kazakh_text(text):
        lang = "kz"

    # Validation: reject empty queries or queries with no alphanumeric characters
    if not text or not re.search(r"[a-zA-Zа-яА-ЯёЁәіңғүұқөһӘІҢҒҮҰҚӨҺ0-9]", text):
        await message.answer(
            f"ℹ️ {t('search_command_content', lang)}",
            parse_mode="HTML",
        )
        return

    result: SmartSearchResult = await smart_search_service.search(text, user_language=lang)

    user = message.from_user
    user_id = user.id if user else 0
    username = user.username if user else None

    # 1. Exact Group Match -> direct display of schedule
    if result.result_type == SearchResultType.EXACT_GROUP and result.exact_group:
        g = result.exact_group
        g_id = g.get("id") or g.get("group_id")
        g_name = g.get("name")
        g_lang = g.get("language") or ("kz" if lang == "kz" else "ru")

        if g_id and g_name:
            register_group_name(g_id, g_name)

        try:
            await user_action_logging_middleware.log_action(
                user_id,
                username,
                "search_exact_group",
                f'Поиск группы "{text}": найдена группа "{g_name}"',
                entity_id=g_id,
                entity_name=g_name,
            )
        except Exception:
            pass

        from src.bot.handlers.callbacks_router import (
            get_current_day_number,
            render_student_day_schedule_message,
        )
        curr_day = get_current_day_number()
        await render_student_day_schedule_message(
            target_msg=message,
            group_id=g_id,
            sch_lang=g_lang,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
            group_name=g_name,
        )
        return

    # 2. Exact Teacher Match -> direct display of teacher schedule
    if result.result_type == SearchResultType.EXACT_TEACHER and result.exact_teacher:
        tch = result.exact_teacher
        t_name = tch.get("fullName") or tch.get("name")
        raw_id = tch.get("id") or tch.get("teacher_id")
        t_id = int(raw_id) if raw_id and str(raw_id).isdigit() else (raw_id or tch.get("name") or t_name)

        if t_id and t_name:
            register_teacher_name(t_id, t_name)

        try:
            await user_action_logging_middleware.log_action(
                user_id,
                username,
                "search_exact_teacher",
                f'Поиск преподавателя "{text}": найден преподаватель "{t_name}"',
                entity_id=t_id,
                entity_name=t_name,
            )
        except Exception:
            pass

        from src.bot.handlers.callbacks_router import (
            get_current_day_number,
            render_teacher_day_schedule_message,
        )
        curr_day = get_current_day_number()
        await render_teacher_day_schedule_message(
            target_msg=message,
            teacher_id=t_id,
            day_num=curr_day,
            ui_lang=lang,
            is_edit=False,
            teacher_name=t_name,
        )
        return

    # 3. Disambiguation list (multiple groups, multiple teachers, or combined)
    if result.result_type in (
        SearchResultType.GROUPS_LIST,
        SearchResultType.TEACHERS_LIST,
        SearchResultType.COMBINED,
    ):
        for grp in (result.groups or []):
            gid = grp.get("id") or grp.get("group_id")
            gname = grp.get("name")
            if gid and gname:
                register_group_name(gid, gname)
        for tch in (result.teachers or []):
            tname = tch.get("fullName") or tch.get("name")
            raw_id = tch.get("id") or tch.get("teacher_id")
            tid = int(raw_id) if raw_id and str(raw_id).isdigit() else (raw_id or tch.get("name") or tname)
            if tid and tname:
                register_teacher_name(tid, tname)

        try:
            await user_action_logging_middleware.log_action(
                user_id,
                username,
                "search_results",
                f'Поиск "{text}": предложен список ({len(result.groups or [])} групп, {len(result.teachers or [])} препод.)',
            )
        except Exception:
            pass

        from src.bot.handlers.callbacks_router import get_current_day_number
        curr_day = get_current_day_number()

        total_matches = len(result.groups) + len(result.teachers)
        heading = (
            f"✨ По вашему запросу: <b>{text}</b> найдено совпадений: <b>{total_matches}</b>\n\n"
            "Выберите нужный вариант из списка:"
        )
        markup = get_search_results_keyboard(
            groups=result.groups,
            teachers=result.teachers,
            lang=lang,
            current_day=curr_day,
            tab="all",
            page=0,
            query=text,
            prefer_groups_first=result.prefer_groups_first,
        )
        await message.answer(heading, reply_markup=markup, parse_mode="HTML")
        return

    # 4. Fuzzy Suggestions ("Did you mean?")
    if result.result_type == SearchResultType.FUZZY_SUGGESTIONS and result.fuzzy_suggestions:
        for s in result.fuzzy_suggestions:
            s_type = getattr(s, "type", None) or (s.get("type") if isinstance(s, dict) else None)
            item = getattr(s, "item", None) or (s.get("item") if isinstance(s, dict) else None)
            if item:
                if s_type == "group":
                    gid = item.get("id") or item.get("group_id")
                    gname = item.get("name")
                    if gid and gname:
                        register_group_name(gid, gname)
                else:
                    tname = item.get("fullName") or item.get("name")
                    raw_id = item.get("id") or item.get("teacher_id")
                    tid = int(raw_id) if raw_id and str(raw_id).isdigit() else (raw_id or item.get("name") or tname)
                    if tid and tname:
                        register_teacher_name(tid, tname)

        try:
            await user_action_logging_middleware.log_action(
                user_id,
                username,
                "search_fuzzy",
                f'Поиск "{text}": опечатка, предложены варианты',
            )
        except Exception:
            pass

        from src.bot.handlers.callbacks_router import get_current_day_number
        curr_day = get_current_day_number()

        prompt = (
            f"🔍 По запросу <b>{text}</b> ничего точного не найдено.\n\n"
            "Возможно, вы имели в виду:"
        )
        markup = get_fuzzy_suggestions_keyboard(
            result.fuzzy_suggestions,
            lang=lang,
            current_day=curr_day,
        )
        await message.answer(prompt, reply_markup=markup, parse_mode="HTML")
        return

    # 5. Not found fallback
    try:
        await user_action_logging_middleware.log_action(
            user_id,
            username,
            "search_not_found",
            f'Поиск "{text}": ничего не найдено',
        )
    except Exception:
        pass

    not_found_msg = (
        f"🔍 К сожалению, по запросу <b>{text}</b> ничего не найдено.\n\n"
        "✍️ Проверьте правильность написания фамилии преподавателя или названия группы.\n"
        "💡 Подробнее о поиске: /search"
    )
    await message.answer(not_found_msg, parse_mode="HTML")


@search_router.callback_query(F.data.startswith("srch|"))
async def handle_search_tabs_and_pagination(call: CallbackQuery) -> None:
    """Handles switching search category filter tabs and navigating pagination."""
    await call.answer()
    raw = call.data or ""
    parts = raw.split("|")
    if len(parts) < 4:
        return

    tab_code = parts[1]
    tab = "grp" if tab_code == "g" else ("tch" if tab_code == "t" else "all")
    page = int(parts[2]) if parts[2].isdigit() else 0
    query = parts[3].strip()

    user_id = call.from_user.id if call.from_user else 0
    lang = await user_service.get_user_language(user_id)
    if is_kazakh_text(query):
        lang = "kz"

    result: SmartSearchResult = await smart_search_service.search(query, user_language=lang)

    from src.bot.handlers.callbacks_router import get_current_day_number
    curr_day = get_current_day_number()
    total_matches = len(result.groups) + len(result.teachers)

    heading = (
        f"✨ По вашему запросу: <b>{query}</b> найдено совпадений: <b>{total_matches}</b>\n\n"
        "Выберите нужный вариант из списка:"
    )

    markup = get_search_results_keyboard(
        groups=result.groups,
        teachers=result.teachers,
        lang=lang,
        current_day=curr_day,
        tab=tab,
        page=page,
        query=query,
        prefer_groups_first=result.prefer_groups_first,
    )

    if call.message and isinstance(call.message, Message):
        try:
            await call.message.edit_text(heading, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                logger.debug(f"Search pagination edit failed: {e}")


__all__ = ["search_router"]
