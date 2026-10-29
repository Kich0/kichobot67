"""Inline keyboard markup builders for Kichobot.

Implements all navigation menus: language selection, faculty/program/group catalogs,
department/teacher catalogs, day-by-day pagination (◀️, 🔄, ▶️), teacher table/text switching,
and search result selectors.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.core.constants import TIMEZONE
from src.core.i18n import t


def get_current_day_number() -> int:
    """Calculates current day of week (0=Mon, 5=Sat) in Asia/Almaty time."""
    now_kz = datetime.now(TIMEZONE)
    day = now_kz.weekday()
    if now_kz.hour >= 18:
        day += 1
    if day > 5:
        day = 0
    return day



def _is_int_like(val: Any) -> bool:
    """Checks whether a value is an integer, whole float, or a string composed purely of digits."""
    if isinstance(val, bool):
        return False
    if isinstance(val, int):
        return True
    if isinstance(val, float):
        return val.is_integer()
    if isinstance(val, str) and val.isdigit():
        return True
    return False


def get_domain_id(item: Any, specific_attr: str = "") -> int:
    """Safely extracts integer domain ID from dict, Beanie Document, or object.

    Prevents Beanie's ObjectId from being mistakenly picked up as domain ID.
    Prefers specific domain attributes (faculty_id/facultyId, program_id/programId, etc.) and item_id descriptor.
    """
    if item is None:
        return 0

    if isinstance(item, dict):
        candidates: list[str] = []
        if specific_attr:
            candidates.append(specific_attr)
            if "_" in specific_attr:
                parts = specific_attr.split("_")
                candidates.append(parts[0] + "".join(p.capitalize() for p in parts[1:]))
        candidates.extend([
            "item_id",
            "id",
            "group_id", "groupId",
            "teacher_id", "teacherId",
            "department_id", "departmentId",
            "faculty_id", "facultyId",
            "program_id", "programId",
        ])
        for key in candidates:
            if key in item:
                val = item.get(key)
                if _is_int_like(val):
                    return int(val)
        return 0

    if specific_attr and hasattr(item, specific_attr):
        val = getattr(item, specific_attr)
        if _is_int_like(val):
            return int(val)
        if "_" in specific_attr:
            parts = specific_attr.split("_")
            camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
            if hasattr(item, camel):
                c_val = getattr(item, camel)
                if _is_int_like(c_val):
                    return int(c_val)

    if hasattr(item, "item_id"):
        try:
            val = item.item_id
            if _is_int_like(val):
                return int(val)
        except Exception:
            pass

    for attr in (
        "faculty_id", "facultyId",
        "program_id", "programId",
        "group_id", "groupId",
        "department_id", "departmentId",
        "teacher_id", "teacherId",
    ):
        if hasattr(item, attr):
            try:
                val = getattr(item, attr)
                if _is_int_like(val):
                    return int(val)
            except Exception:
                pass

    val = getattr(item, "id", None)
    return int(val) if _is_int_like(val) else 0


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to retrieve attributes from dicts, Beanie documents, or objects."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def get_language_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting bot interface language (matches Node.js commit 153)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Русский", callback_data="languageIsRu")],
            [InlineKeyboardButton(text="Қазақ", callback_data="languageIsKz")],
            [
                InlineKeyboardButton(text="\u00A0", callback_data="nothing"),
                InlineKeyboardButton(text="❌", callback_data="delete"),
                InlineKeyboardButton(text="\u00A0", callback_data="nothing"),
            ],
        ]
    )


def get_new_schedule_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Root schedule selector keyboard: student vs teacher vs close (commit 153 parity)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🤓 {t('student', lang)}",
                    callback_data="faculty|0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"👩‍🏫 {t('teacher', lang)}",
                    callback_data="department|0",
                )
            ],
            [
                InlineKeyboardButton(text="\u00A0", callback_data="nothing"),
                InlineKeyboardButton(text="❌", callback_data="delete"),
                InlineKeyboardButton(text="\u00A0", callback_data="nothing"),
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
        f_id = get_domain_id(item, "faculty_id")
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
    buttons.append([InlineKeyboardButton(text=t("go_prev_menu", lang), callback_data="start")])
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
        p_id = get_domain_id(item, "program_id")
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

    buttons.append([InlineKeyboardButton(text=t("go_prev_menu", lang), callback_data="faculty|0")])
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
        g_id = get_domain_id(item, "group_id")
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

    buttons.append([InlineKeyboardButton(text=t("go_prev_menu", lang), callback_data=f"program|{faculty_id}|0")])
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
        d_id = get_domain_id(item, "department_id")
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

    buttons.append([InlineKeyboardButton(text=t("go_prev_menu", lang), callback_data="start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teachers_keyboard(
    teachers: list[Any],
    department_id: int | str,
    page: int = 0,
    lang: str = "ru",
    current_day: int | None = None,
    row_per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of teachers in a department."""
    total = len(teachers)
    page_count = max(0, math.ceil(total / row_per_page) - 1)
    page = max(0, min(page, page_count)) if page_count > 0 else 0

    start_idx = page * row_per_page
    page_items = teachers[start_idx : start_idx + row_per_page]
    day_val = get_current_day_number() if current_day is None else current_day

    buttons: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        t_id = get_domain_id(item, "teacher_id")
        t_name = _get_attr(item, "fullName") or _get_attr(item, "name") or f"Преподаватель {t_id}"
        t_cb_id = _safe_teacher_cb_id(t_id, _get_attr(item, "name") or t_name)
        buttons.append([InlineKeyboardButton(text=str(t_name), callback_data=f"TeacherSchedule|{t_cb_id}|{day_val}")])

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

    buttons.append([InlineKeyboardButton(text=t("go_prev_menu", lang), callback_data="department|0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_schedule_pagination_keyboard(
    pre_callback: str,
    day_number: int,
    back_callback: str,
    lang: str = "ru",
    ad_button: tuple[str, str] | None = None,
    is_halloween: bool = False,
) -> InlineKeyboardMarkup:
    """Day-by-day navigation keyboard: ◀️, 🔄, ▶️, optional ad button, and 🔙."""
    prev_day = day_number - 1 if day_number > 0 else 5
    next_day = day_number + 1 if day_number < 5 else 0

    if is_halloween:
        back_text = f"◀️ 🦇 {t('go_back', lang)}"
        refresh_text = "🔄🎃"
        fwd_text = f"{t('go_forward', lang)} 🦇 ▶️"
        prev_menu_text = f"🕸️ {t('go_prev_menu', lang).replace('◀️ ', '')}"
    else:
        back_text = f"◀️ {t('go_back', lang)}"
        refresh_text = "🔄"
        fwd_text = f"{t('go_forward', lang)} ▶️"
        prev_menu_text = t("go_prev_menu", lang)

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text=back_text, callback_data=f"{pre_callback}|{prev_day}"),
            InlineKeyboardButton(text=refresh_text, callback_data=f"refresh{pre_callback}|{day_number}"),
            InlineKeyboardButton(text=fwd_text, callback_data=f"{pre_callback}|{next_day}"),
        ],
    ]
    if ad_button:
        btn_text, btn_url = ad_button
        rows.append([InlineKeyboardButton(text=btn_text, url=btn_url)])
    rows.append([InlineKeyboardButton(text=prev_menu_text, callback_data=back_callback)])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _safe_teacher_cb_id(raw_id: Any, fallback_name: str = "") -> int | str:
    """Normalizes teacher callback ID and ensures UTF-8 byte length fits within Telegram's 64-byte limit without losing initials."""
    if isinstance(raw_id, int) and raw_id > 0:
        return raw_id
    s = str(raw_id if raw_id else fallback_name).strip()
    if s.isdigit() and int(s) > 0:
        return int(s)

    from src.services.teacher_directory import clean_titles, format_initials

    s = clean_titles(s)
    formatted = format_initials(s)
    if formatted:
        s = formatted.replace(". ", ".")

    max_bytes = 38  # 64 - len("refreshTeacherSchedule||0") == 39 bytes
    if len(s.encode("utf-8")) > max_bytes:
        # Check if initials exist at the end (e.g., " И.О." or " И.")
        m = re.search(r"\s+([A-Za-zА-Яа-яЁёӘәІіҢңҒғҮүҰұҚқӨөҺһ]\.[A-Za-zА-Яа-яЁёӘәІіҢңҒғҮүҰұҚқӨөҺһ]?\.?)$", s)
        if m:
            inits_part = m.group(0)
            surname_part = s[:m.start()]
            while len((surname_part + inits_part).encode("utf-8")) > max_bytes and len(surname_part) > 1:
                surname_part = surname_part[:-1].rstrip()
            s = f"{surname_part}{inits_part}"
        else:
            while len(s.encode("utf-8")) > max_bytes and len(s) > 1:
                s = s[:-1].rstrip()
    return s


def get_teacher_table_keyboard(
    teacher_id: int | str,
    day_number: int = 0,
    department_id: int | str = 0,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """Controls for teacher table PNG image: switch to text, refresh, back."""
    cb_tid = _safe_teacher_cb_id(teacher_id)
    back_cb = f"teacher|{department_id}|0" if department_id and str(department_id) != "0" else "department|0"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📝 {t('schedule_text_view', lang, default='Текстовый вид')}",
                    callback_data=f"teacherText|{cb_tid}|{day_number}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄",
                    callback_data=f"refreshteacherImg|{cb_tid}|{day_number}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("go_prev_menu", lang),
                    callback_data=back_cb,
                )
            ],
        ]
    )


def get_teacher_text_keyboard(
    teacher_id: int | str,
    day_number: int = 0,
    department_id: int | str = 0,
    lang: str = "ru",
    ad_button: tuple[str, str] | None = None,
    is_halloween: bool = False,
) -> InlineKeyboardMarkup:
    """Controls for teacher daily text view: switch to table image, ◀️, 🔄, ▶️, optional ad button, back."""
    cb_tid = _safe_teacher_cb_id(teacher_id)
    prev_day = day_number - 1 if day_number > 0 else 5
    next_day = day_number + 1 if day_number < 5 else 0
    back_cb = f"teacher|{department_id}|0" if department_id and str(department_id) != "0" else "department|0"

    if is_halloween:
        back_text = f"◀️ 🦇 {t('go_back', lang)}"
        refresh_text = "🔄🎃"
        fwd_text = f"{t('go_forward', lang)} 🦇 ▶️"
        img_view_text = f"🖼 🎃 {t('teacher_img_view', lang, default='Таблица недели')}"
        prev_menu_text = f"🕸️ {t('go_prev_menu', lang).replace('◀️ ', '')}"
    else:
        back_text = f"◀️ {t('go_back', lang)}"
        refresh_text = "🔄"
        fwd_text = f"{t('go_forward', lang)} ▶️"
        img_view_text = f"🖼 {t('teacher_img_view', lang, default='Таблица недели')}"
        prev_menu_text = t("go_prev_menu", lang)

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=back_text,
                callback_data=f"TeacherSchedule|{cb_tid}|{prev_day}",
            ),
            InlineKeyboardButton(
                text=refresh_text,
                callback_data=f"refreshTeacherSchedule|{cb_tid}|{day_number}",
            ),
            InlineKeyboardButton(
                text=fwd_text,
                callback_data=f"TeacherSchedule|{cb_tid}|{next_day}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=img_view_text,
                callback_data=f"teacherImg|{cb_tid}|{day_number}",
            )
        ],
    ]
    if ad_button:
        btn_text, btn_url = ad_button
        rows.append([InlineKeyboardButton(text=btn_text, url=btn_url)])
    rows.append([InlineKeyboardButton(text=prev_menu_text, callback_data=back_cb)])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_search_results_keyboard(
    groups: list[dict[str, Any]] | None = None,
    teachers: list[dict[str, Any]] | None = None,
    lang: str = "ru",
    current_day: int | None = None,
    tab: str = "all",
    page: int = 0,
    query: str = "",
    page_size: int = 8,
    prefer_groups_first: bool = True,
) -> InlineKeyboardMarkup:
    """Inline keyboard presenting matched groups and teachers with category filter tabs and pagination."""
    buttons: list[list[InlineKeyboardButton]] = []
    day_val = get_current_day_number() if current_day is None else current_day
    groups_list = groups or []
    teachers_list = teachers or []
    total_g = len(groups_list)
    total_t = len(teachers_list)

    # Clean query for callback (max 18 chars to guarantee byte size <= 64 bytes in Telegram)
    clean_q = re.sub(r"[^\w\s-]", "", query.strip())[:18]

    # Category filter tabs when both groups and teachers are found (or user switched tab)
    if total_g > 0 and total_t > 0:
        btn_all = InlineKeyboardButton(
            text=f"{'🔘 ' if tab == 'all' else ''}{t('all', lang, default='Все')} ({total_g + total_t})",
            callback_data=f"srch|a|0|{clean_q}",
        )
        btn_grp = InlineKeyboardButton(
            text=f"{'🔘 ' if tab == 'grp' else ''}👥 {t('groups', lang, default='Группы')} ({total_g})",
            callback_data=f"srch|g|0|{clean_q}",
        )
        btn_tch = InlineKeyboardButton(
            text=f"{'🔘 ' if tab == 'tch' else ''}👨‍🏫 {t('teachers', lang, default='Преподаватели')} ({total_t})",
            callback_data=f"srch|t|0|{clean_q}",
        )
        buttons.append([btn_all, btn_grp, btn_tch])

    if tab == "grp":
        page_count = max(0, math.ceil(total_g / page_size) - 1)
        page = max(0, min(page, page_count)) if page_count > 0 else 0
        start_idx = page * page_size
        items = groups_list[start_idx : start_idx + page_size]

        for g in items:
            g_id = get_domain_id(g, "group_id")
            g_name = _get_attr(g, "name") or f"Группа {g_id}"
            g_lang = _get_attr(g, "language") or "ru"
            buttons.append([
                InlineKeyboardButton(
                    text=f"👥 {g_name}",
                    callback_data=f"chooseScheduleLanguage|{g_lang}|{g_id}|{day_val}",
                )
            ])

        if page_count > 0:
            prev_p = page - 1 if page > 0 else page_count
            next_p = page + 1 if page < page_count else 0
            buttons.append([
                InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"srch|g|{prev_p}|{clean_q}"),
                InlineKeyboardButton(text=f"{page + 1}/{page_count + 1}", callback_data="nothing"),
                InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"srch|g|{next_p}|{clean_q}"),
            ])

    elif tab == "tch":
        page_count = max(0, math.ceil(total_t / page_size) - 1)
        page = max(0, min(page, page_count)) if page_count > 0 else 0
        start_idx = page * page_size
        items = teachers_list[start_idx : start_idx + page_size]

        for t_item in items:
            t_id = get_domain_id(t_item, "teacher_id")
            t_name = _get_attr(t_item, "fullName") or _get_attr(t_item, "name") or f"Преподаватель {t_id}"
            t_cb_id = _safe_teacher_cb_id(t_id, _get_attr(t_item, "name") or t_name)
            buttons.append([
                InlineKeyboardButton(
                    text=f"👨‍🏫 {t_name}",
                    callback_data=f"TeacherSchedule|{t_cb_id}|{day_val}",
                )
            ])

        if page_count > 0:
            prev_p = page - 1 if page > 0 else page_count
            next_p = page + 1 if page < page_count else 0
            buttons.append([
                InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"srch|t|{prev_p}|{clean_q}"),
                InlineKeyboardButton(text=f"{page + 1}/{page_count + 1}", callback_data="nothing"),
                InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"srch|t|{next_p}|{clean_q}"),
            ])

    else:
        # "all" view
        if total_g > 0 and total_t == 0:
            # Only groups exist: full paginated list
            page_count = max(0, math.ceil(total_g / page_size) - 1)
            page = max(0, min(page, page_count)) if page_count > 0 else 0
            start_idx = page * page_size
            for g in groups_list[start_idx : start_idx + page_size]:
                g_id = get_domain_id(g, "group_id")
                g_name = _get_attr(g, "name") or f"Группа {g_id}"
                g_lang = _get_attr(g, "language") or "ru"
                buttons.append([
                    InlineKeyboardButton(
                        text=f"👥 {g_name}",
                        callback_data=f"chooseScheduleLanguage|{g_lang}|{g_id}|{day_val}",
                    )
                ])
            if page_count > 0:
                prev_p = page - 1 if page > 0 else page_count
                next_p = page + 1 if page < page_count else 0
                buttons.append([
                    InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"srch|a|{prev_p}|{clean_q}"),
                    InlineKeyboardButton(text=f"{page + 1}/{page_count + 1}", callback_data="nothing"),
                    InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"srch|a|{next_p}|{clean_q}"),
                ])
        elif total_t > 0 and total_g == 0:
            # Only teachers exist: full paginated list
            page_count = max(0, math.ceil(total_t / page_size) - 1)
            page = max(0, min(page, page_count)) if page_count > 0 else 0
            start_idx = page * page_size
            for t_item in teachers_list[start_idx : start_idx + page_size]:
                t_id = get_domain_id(t_item, "teacher_id")
                t_name = _get_attr(t_item, "fullName") or _get_attr(t_item, "name") or f"Преподаватель {t_id}"
                t_cb_id = _safe_teacher_cb_id(t_id, _get_attr(t_item, "name") or t_name)
                buttons.append([
                    InlineKeyboardButton(
                        text=f"👨‍🏫 {t_name}",
                        callback_data=f"TeacherSchedule|{t_cb_id}|{day_val}",
                    )
                ])
            if page_count > 0:
                prev_p = page - 1 if page > 0 else page_count
                next_p = page + 1 if page < page_count else 0
                buttons.append([
                    InlineKeyboardButton(text=f"◀️ {t('go_back', lang)}", callback_data=f"srch|a|{prev_p}|{clean_q}"),
                    InlineKeyboardButton(text=f"{page + 1}/{page_count + 1}", callback_data="nothing"),
                    InlineKeyboardButton(text=f"{t('go_forward', lang)} ▶️", callback_data=f"srch|a|{next_p}|{clean_q}"),
                ])
        else:
            # Both exist: balanced preview (first 5 of preferred category, first 5 of other)
            def _render_group_btn(g_item: Any) -> InlineKeyboardButton:
                gid = get_domain_id(g_item, "group_id")
                gname = _get_attr(g_item, "name") or f"Группа {gid}"
                glang = _get_attr(g_item, "language") or "ru"
                return InlineKeyboardButton(
                    text=f"👥 {gname}",
                    callback_data=f"chooseScheduleLanguage|{glang}|{gid}|{day_val}",
                )

            def _render_teacher_btn(t_item: Any) -> InlineKeyboardButton:
                tid = get_domain_id(t_item, "teacher_id")
                tname = _get_attr(t_item, "fullName") or _get_attr(t_item, "name") or f"Преподаватель {tid}"
                tcb_id = _safe_teacher_cb_id(tid, _get_attr(t_item, "name") or tname)
                return InlineKeyboardButton(
                    text=f"👨‍🏫 {tname}",
                    callback_data=f"TeacherSchedule|{tcb_id}|{day_val}",
                )

            if prefer_groups_first:
                for g in groups_list[:5]:
                    buttons.append([_render_group_btn(g)])
                for t_item in teachers_list[:5]:
                    buttons.append([_render_teacher_btn(t_item)])
            else:
                for t_item in teachers_list[:5]:
                    buttons.append([_render_teacher_btn(t_item)])
                for g in groups_list[:5]:
                    buttons.append([_render_group_btn(g)])

    buttons.append([InlineKeyboardButton(text=t("go_prev_menu", lang), callback_data="start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_fuzzy_suggestions_keyboard(
    suggestions: list[Any],
    lang: str = "ru",
    current_day: int | None = None,
) -> InlineKeyboardMarkup:
    """Did-you-mean suggestions inline keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []
    day_val = get_current_day_number() if current_day is None else current_day
    for s in suggestions[:5]:
        s_type = _get_attr(s, "type")
        item = _get_attr(s, "item")
        if s_type == "group":
            g_id = get_domain_id(item, "group_id")
            g_name = _get_attr(item, "name") or f"Группа {g_id}"
            g_lang = _get_attr(item, "language") or "ru"
            buttons.append([
                InlineKeyboardButton(
                    text=f"👥 {g_name}",
                    callback_data=f"chooseScheduleLanguage|{g_lang}|{g_id}|{day_val}",
                )
            ])
        else:
            t_id = get_domain_id(item, "teacher_id")
            t_name = _get_attr(item, "fullName") or _get_attr(item, "name") or f"Преподаватель {t_id}"
            t_cb_id = _safe_teacher_cb_id(t_id, _get_attr(item, "name") or t_name)
            buttons.append([
                InlineKeyboardButton(
                    text=f"👨‍🏫 {t_name}",
                    callback_data=f"TeacherSchedule|{t_cb_id}|{day_val}",
                )
            ])

    buttons.append([InlineKeyboardButton(text=t("go_prev_menu", lang), callback_data="start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


__all__ = [
    "get_departments_keyboard",
    "get_domain_id",
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
