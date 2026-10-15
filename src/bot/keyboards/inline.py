"""Inline keyboard markup builders for Kichobot.

Implements all navigation menus: language selection, faculty/program/group catalogs,
department/teacher catalogs, day-by-day pagination (◀️, 🔄, ▶️), teacher table/text switching,
and search result selectors.
"""

from __future__ import annotations

import math
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.core.i18n import t


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to retrieve attributes from dicts, Beanie documents, or objects."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def get_language_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting bot interface language."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="languageIsru"),
                InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="languageIskz"),
            ]
        ]
    )


def get_new_schedule_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Root schedule selector keyboard: student vs teacher vs search."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎓 {t('student_schedule', lang)}",
                    callback_data="faculty|0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"👨‍🏫 {t('teacher_schedule', lang)}",
                    callback_data="department|0",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Тіл / Язык",
                    callback_data="language_select",
                )
            ],
        ]
    )


def get_faculties_keyboard(
    faculties: list[Any],
    page: int = 0,
    lang: str = "ru",
    row_per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of university faculties."""
    total = len(faculties)
    page_count = max(0, math.ceil(total / row_per_page) - 1)
    page = max(0, min(page, page_count)) if page_count > 0 else 0

    start_idx = page * row_per_page
    page_items = faculties[start_idx : start_idx + row_per_page]

    buttons: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        f_id = _get_attr(item, "id") or _get_attr(item, "faculty_id")
        f_name = _get_attr(item, "name") or f"Факультет {f_id}"
        buttons.append([InlineKeyboardButton(text=str(f_name), callback_data=f"program|{f_id}|0")])

    # Pagination controls
    if page_count > 0:
        prev_page = page - 1 if page > 0 else page_count
        next_page = page + 1 if page < page_count else 0
        buttons.append(
            [
                InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"faculty|{prev_page}"),
                InlineKeyboardButton(
                    text=t("page_mini_text", lang, page=page + 1, pageCount=page_count + 1),
                    callback_data="nothing",
                ),
                InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"faculty|{next_page}"),
            ]
        )

    # Return to root menu
    buttons.append([InlineKeyboardButton(text=f"🔙 {t('go_prev_menu', lang)}", callback_data="start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_programs_keyboard(
    programs: list[Any],
    faculty_id: int | str,
    page: int = 0,
    lang: str = "ru",
    row_per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of educational programs."""
    total = len(programs)
    page_count = max(0, math.ceil(total / row_per_page) - 1)
    page = max(0, min(page, page_count)) if page_count > 0 else 0

    start_idx = page * row_per_page
    page_items = programs[start_idx : start_idx + row_per_page]

    buttons: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        p_id = _get_attr(item, "id") or _get_attr(item, "program_id")
        p_name = _get_attr(item, "name") or f"Программа {p_id}"
        buttons.append([InlineKeyboardButton(text=str(p_name), callback_data=f"group|{faculty_id}|{p_id}|0")])

    if page_count > 0:
        prev_page = page - 1 if page > 0 else page_count
        next_page = page + 1 if page < page_count else 0
        buttons.append(
            [
                InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"program|{faculty_id}|{prev_page}"),
                InlineKeyboardButton(
                    text=t("page_mini_text", lang, page=page + 1, pageCount=page_count + 1),
                    callback_data="nothing",
                ),
                InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"program|{faculty_id}|{next_page}"),
            ]
        )

    buttons.append([InlineKeyboardButton(text=f"🔙 {t('go_prev_menu', lang)}", callback_data="faculty|0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_groups_keyboard(
    groups: list[Any],
    faculty_id: int | str,
    program_id: int | str,
    page: int = 0,
    lang: str = "ru",
    current_day: int = 0,
    row_per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of student groups."""
    total = len(groups)
    page_count = max(0, math.ceil(total / row_per_page) - 1)
    page = max(0, min(page, page_count)) if page_count > 0 else 0

    start_idx = page * row_per_page
    page_items = groups[start_idx : start_idx + row_per_page]

    buttons: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        g_id = _get_attr(item, "id") or _get_attr(item, "group_id")
        g_name = _get_attr(item, "name") or f"Группа {g_id}"
        g_lang = _get_attr(item, "language") or "ru"
        buttons.append([
            InlineKeyboardButton(
                text=str(g_name),
                callback_data=f"chooseScheduleLanguage|{g_lang}|{g_id}|{current_day}",
            )
        ])

    if page_count > 0:
        prev_page = page - 1 if page > 0 else page_count
        next_page = page + 1 if page < page_count else 0
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"◀️ {t('go_back', lang)}",
                    callback_data=f"group|{faculty_id}|{program_id}|{prev_page}",
                ),
                InlineKeyboardButton(
                    text=t("page_mini_text", lang, page=page + 1, pageCount=page_count + 1),
                    callback_data="nothing",
                ),
                InlineKeyboardButton(
                    text=f"{t('go_forward', lang)} ▶️",
                    callback_data=f"group|{faculty_id}|{program_id}|{next_page}",
                ),
            ]
        )

    buttons.append([InlineKeyboardButton(text=f"🔙 {t('go_prev_menu', lang)}", callback_data=f"program|{faculty_id}|0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_departments_keyboard(
    departments: list[Any],
    page: int = 0,
    lang: str = "ru",
    row_per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of university departments (кафедры)."""
    total = len(departments)
    page_count = max(0, math.ceil(total / row_per_page) - 1)
    page = max(0, min(page, page_count)) if page_count > 0 else 0

    start_idx = page * row_per_page
    page_items = departments[start_idx : start_idx + row_per_page]

    buttons: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        d_id = _get_attr(item, "id") or _get_attr(item, "department_id")
        d_name = _get_attr(item, "name") or f"Кафедра {d_id}"
        buttons.append([InlineKeyboardButton(text=str(d_name), callback_data=f"teacher|{d_id}|0")])

    if page_count > 0:
        prev_page = page - 1 if page > 0 else page_count
        next_page = page + 1 if page < page_count else 0
        buttons.append(
            [
                InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"department|{prev_page}"),
                InlineKeyboardButton(
                    text=t("page_mini_text", lang, page=page + 1, pageCount=page_count + 1),
                    callback_data="nothing",
                ),
                InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"department|{next_page}"),
            ]
        )

    buttons.append([InlineKeyboardButton(text=f"🔙 {t('go_prev_menu', lang)}", callback_data="start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teachers_keyboard(
    teachers: list[Any],
    department_id: int | str,
    page: int = 0,
    lang: str = "ru",
    row_per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of teachers in a department."""
    total = len(teachers)
    page_count = max(0, math.ceil(total / row_per_page) - 1)
    page = max(0, min(page, page_count)) if page_count > 0 else 0

    start_idx = page * row_per_page
    page_items = teachers[start_idx : start_idx + row_per_page]

    buttons: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        t_id = _get_attr(item, "id") or _get_attr(item, "teacher_id")
        t_name = _get_attr(item, "fullName") or _get_attr(item, "name") or f"Преподаватель {t_id}"
        buttons.append([InlineKeyboardButton(text=str(t_name), callback_data=f"teacherImg|{t_id}|0")])

    if page_count > 0:
        prev_page = page - 1 if page > 0 else page_count
        next_page = page + 1 if page < page_count else 0
        buttons.append(
            [
                InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"teacher|{department_id}|{prev_page}"),
                InlineKeyboardButton(
                    text=t("page_mini_text", lang, page=page + 1, pageCount=page_count + 1),
                    callback_data="nothing",
                ),
                InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"teacher|{department_id}|{next_page}"),
            ]
        )

    buttons.append([InlineKeyboardButton(text=f"🔙 {t('go_prev_menu', lang)}", callback_data="department|0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_schedule_pagination_keyboard(
    pre_callback: str,
    day_number: int,
    back_callback: str,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """Day-by-day navigation keyboard: ◀️, 🔄, ▶️ and 🔙."""
    prev_day = day_number - 1 if day_number > 0 else 5
    next_day = day_number + 1 if day_number < 5 else 0

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"{pre_callback}|{prev_day}"),
                InlineKeyboardButton(text="🔄", callback_data=f"refresh{pre_callback}|{day_number}"),
                InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"{pre_callback}|{next_day}"),
            ],
            [
                InlineKeyboardButton(text=f"🔙 {t('go_prev_menu', lang)}", callback_data=back_callback)
            ],
        ]
    )


def get_teacher_table_keyboard(
    teacher_id: int | str,
    day_number: int = 0,
    department_id: int | str = 0,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """Controls for teacher table PNG image: switch to text, refresh, back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📝 {t('schedule_text_view', lang, default='Текстовый вид')}",
                    callback_data=f"teacherText|{teacher_id}|{day_number}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄",
                    callback_data=f"refreshteacherImg|{teacher_id}|{day_number}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔙 {t('go_prev_menu', lang)}",
                    callback_data=f"teacher|{department_id}|0",
                )
            ],
        ]
    )


def get_teacher_text_keyboard(
    teacher_id: int | str,
    day_number: int = 0,
    department_id: int | str = 0,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """Controls for teacher daily text view: switch to table image, ◀️, 🔄, ▶️, back."""
    prev_day = day_number - 1 if day_number > 0 else 5
    next_day = day_number + 1 if day_number < 5 else 0

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🖼 {t('teacher_img_view', lang, default='Таблица недели')}",
                    callback_data=f"teacherImg|{teacher_id}|{day_number}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"◀️ {t('go_back', lang)}",
                    callback_data=f"TeacherSchedule|{teacher_id}|{prev_day}",
                ),
                InlineKeyboardButton(
                    text="🔄",
                    callback_data=f"refreshTeacherSchedule|{teacher_id}|{day_number}",
                ),
                InlineKeyboardButton(
                    text=f"{t('go_forward', lang)} ▶️",
                    callback_data=f"TeacherSchedule|{teacher_id}|{next_day}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"🔙 {t('go_prev_menu', lang)}",
                    callback_data=f"teacher|{department_id}|0",
                )
            ],
        ]
    )


def get_search_results_keyboard(
    groups: list[dict[str, Any]] | None = None,
    teachers: list[dict[str, Any]] | None = None,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """Inline keyboard presenting matched groups and teachers from smart search."""
    buttons: list[list[InlineKeyboardButton]] = []

    if groups:
        for g in groups[:8]:
            g_id = g.get("id") or g.get("group_id")
            g_name = g.get("name") or f"Группа {g_id}"
            g_lang = g.get("language") or "ru"
            buttons.append([
                InlineKeyboardButton(
                    text=f"👥 {g_name}",
                    callback_data=f"chooseScheduleLanguage|{g_lang}|{g_id}|0",
                )
            ])

    if teachers:
        for t_item in teachers[:8]:
            t_id = t_item.get("id") or t_item.get("teacher_id")
            t_name = t_item.get("fullName") or t_item.get("name") or f"Преподаватель {t_id}"
            buttons.append([
                InlineKeyboardButton(
                    text=f"👨‍🏫 {t_name}",
                    callback_data=f"teacherImg|{t_id}|0",
                )
            ])

    buttons.append([InlineKeyboardButton(text=f"🔙 {t('go_prev_menu', lang)}", callback_data="start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_fuzzy_suggestions_keyboard(
    suggestions: list[Any],
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """Did-you-mean suggestions inline keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []
    for s in suggestions[:5]:
        s_type = _get_attr(s, "type")
        item = _get_attr(s, "item")
        if s_type == "group":
            g_id = _get_attr(item, "id") or _get_attr(item, "group_id")
            g_name = _get_attr(item, "name") or f"Группа {g_id}"
            g_lang = _get_attr(item, "language") or "ru"
            buttons.append([
                InlineKeyboardButton(
                    text=f"👥 {g_name}",
                    callback_data=f"chooseScheduleLanguage|{g_lang}|{g_id}|0",
                )
            ])
        else:
            t_id = _get_attr(item, "id") or _get_attr(item, "teacher_id")
            t_name = _get_attr(item, "fullName") or _get_attr(item, "name") or f"Преподаватель {t_id}"
            buttons.append([
                InlineKeyboardButton(
                    text=f"👨‍🏫 {t_name}",
                    callback_data=f"teacherImg|{t_id}|0",
                )
            ])

    buttons.append([InlineKeyboardButton(text=f"🔙 {t('go_prev_menu', lang)}", callback_data="start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


__all__ = [
    "get_departments_keyboard",
    "get_faculties_keyboard",
    "get_fuzzy_suggestions_keyboard",
    "get_groups_keyboard",
    "get_language_keyboard",
    "get_new_schedule_keyboard",
    "get_programs_keyboard",
    "get_schedule_pagination_keyboard",
    "get_search_results_keyboard",
    "get_teacher_table_keyboard",
    "get_teacher_text_keyboard",
    "get_teachers_keyboard",
]
