"""Bot callback queries router.

Handles inline button clicks:
- Language selection (languageIsru, languageIskz)
- Navigation: start, delete, nothing
- Faculty, program, group catalogs
- Department, teacher catalogs
- Student schedule day pagination & refresh (10m cooldown, 30m RAM cache)
- Teacher schedule table image & text view (20m image cooldown, 5m text cooldown)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from typing import Any

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from cachetools import TTLCache

from src.services.group_sorting import sort_groups, sort_teachers

from src.bot.keyboards.inline import (
    _get_attr,
    get_departments_keyboard,
    get_domain_id,
    get_faculties_keyboard,
    get_groups_keyboard,
    get_language_keyboard,
    get_new_schedule_keyboard,
    get_programs_keyboard,
    get_schedule_pagination_keyboard,
    get_teacher_table_keyboard,
    get_teacher_text_keyboard,
    get_teachers_keyboard,
)
from src.bot.keyboards.reply import get_main_reply_keyboard
from src.bot.middlewares.user_action_logger import (
    register_department_name,
    register_faculty_name,
    register_group_name,
    register_program_name,
    register_teacher_name,
)
from src.core.constants import (
    CACHE_CAPACITY_STUDENT_GROUPS,
    CACHE_CAPACITY_TEACHERS,
    COOLDOWN_STUDENT_REFRESH,
    COOLDOWN_TEACHER_IMAGE_REFRESH,
    COOLDOWN_TEACHER_TEXT_REFRESH,
    TIMEZONE,
)
from src.core.i18n import t
from src.core.logging import get_logger
from src.services.buketov_api import buketov_api_service
from src.services.schedule_adapter import schedule_api_adapter
from src.services.table_image import table_image_service
from src.services.teacher_directory import clean_titles, teacher_directory_service
from src.database.connection import get_client
from src.services.user_service import user_service

logger = get_logger("callbacks_router")
callbacks_router = Router(name="callbacks_router")

# In-memory RAM caches for schedule viewing (bounded LRU with TTL matching AGENTS.md §4)
# Key: f"{group_id}_{lang}" -> {"data": [...], "timestamp": float, "group": Any, "is_from_db": bool}
student_schedule_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=360, ttl=1800.0)

# Key: f"{teacher_id}_{lang}" -> {"data": [...], "timestamp": float, "teacher": Any, "is_from_db": bool}
teacher_text_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=360, ttl=1800.0)

# Key: str(teacher_id) -> {"data": [...], "timestamp": float, "teacher": Any, "is_from_db": bool}
teacher_table_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=100, ttl=1200.0)

# Deduplication hashes for background MongoDB persistence & metadata caches for 0ms pagination
_persisted_student_hashes: TTLCache[tuple[int, int | str], str] = TTLCache(maxsize=500, ttl=3600.0)
_persisted_teacher_hashes: TTLCache[tuple[int, int | str], str] = TTLCache(maxsize=500, ttl=3600.0)
_group_meta_cache: TTLCache[tuple[int, int | str], dict[str, Any]] = TTLCache(maxsize=1000, ttl=3600.0)
_teacher_meta_cache: TTLCache[tuple[int, int | str], dict[str, Any]] = TTLCache(maxsize=1000, ttl=3600.0)


async def resolve_teacher_identity(
    teacher_id: int | str,
    teacher_name: str | None = None,
) -> tuple[int | str, str, int, str]:
    """Resolves any teacher reference (integer ID, string ID, initials, or full name) into:
    (canonical_id, display_name, department_id, lookup_name)
    """
    client_id = id(get_client())
    cached_meta = _teacher_meta_cache.get((client_id, teacher_id))
    if cached_meta is not None:
        can_id = cached_meta.get("canonical_id", teacher_id)
        if isinstance(can_id, str) and can_id.isdigit():
            can_id = int(can_id)
        return (
            can_id,
            cached_meta.get("name") or teacher_name or str(teacher_id),
            cached_meta.get("dept_id", 0),
            cached_meta.get("lookup_name") or cached_meta.get("name") or str(teacher_id),
        )

    if teacher_name and (client_id, teacher_name) in _teacher_meta_cache:
        cached_meta = _teacher_meta_cache[(client_id, teacher_name)]
        can_id = cached_meta.get("canonical_id", teacher_id)
        if isinstance(can_id, str) and can_id.isdigit():
            can_id = int(can_id)
        return (
            can_id,
            cached_meta.get("name") or teacher_name,
            cached_meta.get("dept_id", 0),
            cached_meta.get("lookup_name") or cached_meta.get("name") or str(teacher_id),
        )

    t_doc = None
    canonical_id: int | str = teacher_id
    display_name = teacher_name or (f"ID {teacher_id}" if isinstance(teacher_id, int) or str(teacher_id).isdigit() else str(teacher_id))
    lookup_name = ""
    dept_id = 0
    is_head = False
    job_title = ""

    try:
        from src.database.models.teacher import Teacher

        if isinstance(teacher_id, int) or (isinstance(teacher_id, str) and teacher_id.isdigit()):
            num_id = int(teacher_id)
            canonical_id = num_id
            t_doc = await Teacher.find_one({"id": num_id}) or await Teacher.find_one({"teacherId": num_id})
        else:
            str_name = str(teacher_id).strip()
            # Try finding by exact name in DB
            t_doc = await Teacher.find_one({"name": str_name})
            if not t_doc and teacher_name:
                t_doc = await Teacher.find_one({"name": teacher_name})
            if not t_doc:
                # Try finding match via directory service
                matched_dir = teacher_directory_service.find_match(str_name) or (
                    teacher_directory_service.find_match(teacher_name) if teacher_name else None
                )
                if matched_dir:
                    m_initials = matched_dir.get("initials")
                    m_full = matched_dir.get("fullName")
                    if m_initials:
                        clean_inits = clean_titles(m_initials)
                        t_doc = await Teacher.find_one({"name": clean_inits})
                    if not t_doc and m_full:
                        surname = m_full.split()[0]
                        candidates = await Teacher.find({"name": {"$regex": surname, "$options": "i"}}).to_list()
                        for c in candidates:
                            c_name = getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else "")
                            if buketov_api_service.match_teacher_record(c_name, m_full):
                                t_doc = c
                                break

        if t_doc:
            doc_id = getattr(t_doc, "teacher_id", None) or getattr(t_doc, "id", None) or (t_doc.get("id") if isinstance(t_doc, dict) else None)
            if doc_id and str(doc_id).isdigit():
                canonical_id = int(doc_id)
            lookup_name = getattr(t_doc, "name", "") or (t_doc.get("name", "") if isinstance(t_doc, dict) else "")
            doc_dict = t_doc.model_dump(by_alias=True) if hasattr(t_doc, "model_dump") else (t_doc if isinstance(t_doc, dict) else {"id": canonical_id, "name": lookup_name})
            enriched = teacher_directory_service.enrich(doc_dict)
            display_name = (enriched.get("fullName") if enriched else None) or lookup_name or display_name
            dept_val = getattr(t_doc, "department", 0) or (t_doc.get("department") if isinstance(t_doc, dict) else 0)
            dept_id = int(dept_val) if dept_val and str(dept_val).isdigit() else 0
            is_head = bool((enriched.get("isHead") if enriched else False) or getattr(t_doc, "is_head", False) or getattr(t_doc, "isHead", False))
            job_title = (enriched.get("jobTitle") if enriched else "") or getattr(t_doc, "job_title", "") or getattr(t_doc, "jobTitle", "")
        elif isinstance(teacher_id, str) and not teacher_id.isdigit():
            matched_dir = teacher_directory_service.find_match(teacher_id) or (
                teacher_directory_service.find_match(teacher_name) if teacher_name else None
            )
            if matched_dir:
                display_name = matched_dir.get("fullName") or matched_dir.get("name") or display_name
                lookup_name = matched_dir.get("name") or display_name
                dept_val = matched_dir.get("departmentId") or 0
                dept_id = int(dept_val) if dept_val and str(dept_val).isdigit() else 0
                is_head = bool(matched_dir.get("isHead", False))
                job_title = matched_dir.get("jobTitle", "")
            else:
                lookup_name = display_name
    except Exception as e:
        logger.debug(f"Error in resolve_teacher_identity for {teacher_id}: {e}")

    if not lookup_name:
        lookup_name = display_name

    meta = {
        "canonical_id": canonical_id,
        "name": display_name,
        "dept_id": dept_id,
        "lookup_name": lookup_name,
        "is_head": is_head,
        "job_title": job_title,
    }
    _teacher_meta_cache[(client_id, canonical_id)] = meta
    _teacher_meta_cache[(client_id, teacher_id)] = meta
    _teacher_meta_cache[(client_id, display_name)] = meta
    if lookup_name and lookup_name != display_name:
        _teacher_meta_cache[(client_id, lookup_name)] = meta

    if canonical_id and display_name:
        register_teacher_name(canonical_id, display_name)
    if teacher_id and teacher_id != canonical_id and display_name:
        register_teacher_name(teacher_id, display_name)

    return canonical_id, display_name, dept_id, lookup_name



def _norm_cache_lang(lang: str | None) -> str:
    """Normalizes schedule/UI language ('рус'/'ru' -> 'ru', 'каз'/'kz' -> 'kz') for cache keys."""
    if not lang:
        return "ru"
    return "kz" if str(lang).lower().strip() in ("kz", "каз", "kaz", "қаз", "kk") else "ru"


def clear_schedule_caches() -> None:
    """Clears all in-memory schedule caches (for testing and manual reset)."""
    student_schedule_cache.clear()
    teacher_text_cache.clear()
    teacher_table_cache.clear()
    _persisted_student_hashes.clear()
    _persisted_teacher_hashes.clear()
    _group_meta_cache.clear()
    _teacher_meta_cache.clear()
    user_service.clear_cache()


def get_current_day_number() -> int:
    """Calculates current day of week (0=Mon, 5=Sat) in Asia/Almaty time.

    Switches to the next day after 18:00.
    """
    now_kz = datetime.now(TIMEZONE)
    day = (now_kz.weekday())  # 0 is Mon, 6 is Sun
    if now_kz.hour >= 18:
        day += 1
    if day > 5:
        day = 0
    return day


def format_elapsed_time(timestamp: float, lang: str = "ru", is_from_db: bool = False) -> str:
    """Formats elapsed time indicator with emoji badges (🟢 / 🟡 / 🔴 / 🔵)."""
    now = time.time()
    diff = max(0, int(now - timestamp))
    hours = diff // 3600

    status_emoji = "🔵" if is_from_db else "🟢"
    if not is_from_db:
        if hours >= 24:
            status_emoji = "🔴"
        elif hours >= 5:
            status_emoji = "🟡"

    if diff < 60:
        return f"{status_emoji} {diff} {t('second', lang)}"
    elif diff < 3600:
        mins = diff // 60
        secs = diff % 60
        if secs > 0:
            return f"{status_emoji} {mins} {t('minute', lang)} {secs} {t('second', lang)}"
        return f"{status_emoji} {mins} {t('minute', lang)}"
    elif diff < 86400:
        return f"{status_emoji} {hours} {t('hour', lang)}"
    else:
        days = diff // 86400
        return f"{status_emoji} {days} {t('day', lang)}"


def format_timestamp_clock(timestamp: float) -> str:
    """Formats clock string HH:MM:SS in Asia/Almaty timezone."""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(TIMEZONE)
    return dt.strftime("%H:%M:%S")


def transform_group_string(input_string: str) -> str:
    """Transforms group string replacing (room/building) with (Ауд. room | building корпус).

    Replicates Node.js commit 25601dc TeacherScheduleController.transformGroupString.
    """
    if not input_string:
        return ""
    regex = r"\s*\(([^/]+)/([^)]+)\)"
    result_string = re.sub(regex, r" (Ауд. \1 | \2 корпус)", input_string)
    parts = result_string.split(") ")
    if len(parts) > 1:
        for i in range(len(parts) - 1):
            parts[i] += ")\n"
        result_string = "".join(parts)
    return result_string.strip()


def add_symbol_to_each_line(input_string: str, symbol: str) -> str:
    """Prefixes each line with symbol. Replicates Node.js addSymbolToEachLine."""
    if not input_string:
        return ""
    lines = input_string.split("\n")
    return "\n".join(f"{symbol} {line}" for line in lines)


def format_subject_with_lesson_type(subject: str, lesson_type: str | None = None) -> str:
    """Standardizes subject and lesson type badge matching Node.js commit 25601dc."""
    if not subject:
        return ""
    clean_subject = subject.strip()

    clean_subject = re.sub(r"\s*\((?:лекц?|лек)\)", " /Лекция/", clean_subject, flags=re.IGNORECASE)
    clean_subject = re.sub(r"\s*/(?:лекц?|лек)/", " /Лекция/", clean_subject, flags=re.IGNORECASE)
    clean_subject = re.sub(
        r"\s*\((?:прак(?:тическое)?|семин(?:ар)?|пр)\)",
        " /Прак.зан./",
        clean_subject,
        flags=re.IGNORECASE,
    )
    clean_subject = re.sub(
        r"\s*/(?:прак(?:тическое)?|семин(?:ар)?|пр)/",
        " /Прак.зан./",
        clean_subject,
        flags=re.IGNORECASE,
    )
    clean_subject = re.sub(
        r"\s*\((?:лаб(?:ораторное)?|лаб)\)",
        " /Лаб.раб./",
        clean_subject,
        flags=re.IGNORECASE,
    )
    clean_subject = re.sub(
        r"\s*/(?:лаб(?:ораторное)?|лаб)/",
        " /Лаб.раб./",
        clean_subject,
        flags=re.IGNORECASE,
    )
    clean_subject = re.sub(r"\s*\((?:сроп)\)", " /СРОП/", clean_subject, flags=re.IGNORECASE)
    clean_subject = re.sub(r"\s*/(?:сроп)/", " /СРОП/", clean_subject, flags=re.IGNORECASE)

    has_type_already = bool(
        re.search(r"/(?:Лекция|Прак\.зан\.|Лаб\.раб\.|СРОП)/", clean_subject, flags=re.IGNORECASE)
    )
    if not has_type_already and lesson_type:
        lower = lesson_type.lower().strip()
        type_badge = ""
        if "лекц" in lower or lower == "лек":
            type_badge = "/Лекция/"
        elif "прак" in lower or "семин" in lower or lower == "пр":
            type_badge = "/Прак.зан./"
        elif "лаб" in lower:
            type_badge = "/Лаб.раб./"
        elif "сроп" in lower:
            type_badge = "/СРОП/"
        elif lesson_type.strip():
            type_badge = f"/{lesson_type.strip()}/"

        if type_badge:
            clean_subject = f"{clean_subject} {type_badge}"

    return clean_subject.strip()


async def safe_edit_or_replace_message(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
) -> None:
    """Safely edits text message in-place, or deletes photo/stale message and sends a new one."""
    if not message:
        return

    # If message contains photo/media, edit_text is forbidden by Telegram Bot API
    if message.photo:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
        return

    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return
        # If editing text failed (e.g. photo without message.photo populated or deleted message), delete and send fresh
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )


# === Basic Navigation Callbacks ===

@callbacks_router.callback_query(F.data == "nothing")
async def handle_nothing(call: CallbackQuery) -> None:
    """No-op callback."""
    await call.answer()


@callbacks_router.callback_query(F.data == "delete")
async def handle_delete(call: CallbackQuery) -> None:
    """Deletes the current bot message."""
    await call.answer()
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            pass


@callbacks_router.callback_query(F.data == "start")
async def handle_back_to_start(call: CallbackQuery) -> None:
    """Returns to root schedule selector menu."""
    await call.answer()
    user_id = call.from_user.id if call.from_user else 0
    lang = await user_service.get_user_language(user_id)

    prompt = (
        f"👋 {t('new_schedule_menu_content', lang)}\n"
        f"<i>{t('new_schedule_menu_hint', lang)}</i>\n"
        f"{t('new_schedule_menu_hint2', lang)}"
    )

    if call.message and isinstance(call.message, Message):
        await safe_edit_or_replace_message(
            call.message,
            prompt,
            reply_markup=get_new_schedule_keyboard(lang),
            parse_mode="HTML",
        )


@callbacks_router.callback_query(F.data == "language_select")
async def handle_language_select_menu(call: CallbackQuery) -> None:
    """Opens language selection menu."""
    await call.answer()
    prompt = (
        "🌍 Выберите язык интерфейса. \n"
        "🌍 Интерфейстің тіл тандаңыз. "
    )
    if call.message and isinstance(call.message, Message):
        await safe_edit_or_replace_message(
            call.message,
            prompt,
            reply_markup=get_language_keyboard(),
        )


@callbacks_router.callback_query(F.data.startswith("languageIs"))
async def handle_set_language(call: CallbackQuery) -> None:
    """Changes user preferred language and redirects to welcome page with main reply keyboard."""
    await call.answer()
    code = (call.data or "").replace("languageIs", "").lower()
    user_id = call.from_user.id if call.from_user else 0
    await user_service.set_user_language(user_id, code)

    lang = "kz" if code == "kz" else "ru"
    msg_text = t("welcome_page", lang)
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(
            msg_text,
            reply_markup=get_main_reply_keyboard(lang),
            parse_mode="HTML",
        )


# === Faculty, Program, Group Selection ===

@callbacks_router.callback_query(F.data.startswith("faculty|"))
async def handle_faculty_menu(call: CallbackQuery) -> None:
    """Displays paginated faculties list."""
    await call.answer()
    user_id = call.from_user.id if call.from_user else 0
    lang = await user_service.get_user_language(user_id)

    parts = (call.data or "").split("|")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    try:
        from src.database.models.faculty import Faculty

        faculties = await Faculty.find().sort("id").to_list()
        for f in faculties:
            f_id = get_domain_id(f, "faculty_id")
            f_name = _get_attr(f, "name") or _get_attr(f, "fullName")
            if f_id and f_name:
                register_faculty_name(f_id, f_name)
    except Exception:
        faculties = []

    markup = get_faculties_keyboard(faculties, page=page, lang=lang)
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(faculties) + 9) // 10))
    msg_text = f"{t('faculty_pick', lang)} \n{page_text}"

    if call.message and isinstance(call.message, Message):
        await safe_edit_or_replace_message(call.message, msg_text, reply_markup=markup, parse_mode="HTML")


@callbacks_router.callback_query(F.data.startswith("program|"))
async def handle_program_menu(call: CallbackQuery) -> None:
    """Displays paginated educational programs for a faculty."""
    await call.answer()
    user_id = call.from_user.id if call.from_user else 0
    lang = await user_service.get_user_language(user_id)

    parts = (call.data or "").split("|")
    faculty_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    try:
        from src.database.models.faculty import Faculty
        from src.database.models.program import Program

        programs = await Program.find({"faculty": faculty_id}).sort("name").to_list()
        for p in programs:
            p_id = get_domain_id(p, "program_id")
            p_name = _get_attr(p, "name")
            if p_id and p_name:
                register_program_name(p_id, p_name)

        faculty = await Faculty.find_one({"id": faculty_id})
        faculty_name = faculty.name if faculty else f"ID {faculty_id}"
        if faculty_id and faculty_name:
            register_faculty_name(faculty_id, faculty_name)
    except Exception:
        programs = []
        faculty_name = f"ID {faculty_id}"

    markup = get_programs_keyboard(programs, faculty_id=faculty_id, page=page, lang=lang)
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(programs) + 9) // 10))
    msg_text = f"{t('program_pick', lang)}\n{t('faculty', lang, faculty=faculty_name)}\n{page_text}"

    if call.message and isinstance(call.message, Message):
        await safe_edit_or_replace_message(call.message, msg_text, reply_markup=markup, parse_mode="HTML")


@callbacks_router.callback_query(F.data.startswith("group|"))
async def handle_group_menu(call: CallbackQuery) -> None:
    """Displays paginated student groups for a program."""
    await call.answer()
    user_id = call.from_user.id if call.from_user else 0
    lang = await user_service.get_user_language(user_id)

    parts = (call.data or "").split("|")
    faculty_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    program_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

    try:
        from src.database.models.group import Group
        from src.database.models.program import Program

        raw_groups = await Group.find({"program": program_id}).to_list()
        groups = sort_groups(raw_groups)
        for g in groups:
            g_id = get_domain_id(g, "group_id")
            g_name = _get_attr(g, "name")
            if g_id and g_name:
                register_group_name(g_id, g_name)

        program = await Program.find_one({"id": program_id})
        program_name = program.name if program else f"ID {program_id}"
        if program_id and program_name:
            register_program_name(program_id, program_name)
    except Exception:
        groups = []
        program_name = f"ID {program_id}"

    curr_day = get_current_day_number()
    markup = get_groups_keyboard(
        groups,
        faculty_id=faculty_id,
        program_id=program_id,
        page=page,
        lang=lang,
        current_day=curr_day,
    )
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(groups) + 9) // 10))
    msg_text = f"{t('group_pick', lang)}\n{t('program', lang, program=program_name)}\n{page_text}"

    if call.message and isinstance(call.message, Message):
        await safe_edit_or_replace_message(call.message, msg_text, reply_markup=markup, parse_mode="HTML")


async def _bg_persist_student_schedule(group_id: int, adapted_data: list[Any]) -> None:
    """Quietly saves fresh student schedule into MongoDB Schedule collection as emergency fallback."""
    if not group_id or not adapted_data:
        return
    try:
        from src.database.models.schedule import Schedule

        num_id = int(group_id) if str(group_id).isdigit() else group_id
        client_id = id(get_client())
        data_json = json.dumps(adapted_data, ensure_ascii=False, sort_keys=True, default=str)
        sch_hash = hashlib.sha256(data_json.encode("utf-8")).hexdigest()
        if _persisted_student_hashes.get((client_id, num_id)) == sch_hash:
            return

        now_dt = datetime.now(timezone.utc)
        doc = await Schedule.find_one({"groupId": num_id})
        if doc:
            if doc.hash == sch_hash:
                _persisted_student_hashes[(client_id, num_id)] = sch_hash
                return
            doc.data = adapted_data
            doc.hash = sch_hash
            doc.checked_at = now_dt
            doc.updated_at = now_dt
            await doc.save()
        else:
            new_doc = Schedule(
                groupId=num_id,
                data=adapted_data,
                hash=sch_hash,
                checkedAt=now_dt,
                updatedAt=now_dt,
            )
            await new_doc.insert()
        _persisted_student_hashes[(client_id, num_id)] = sch_hash
    except Exception as e:
        logger.debug(f"Background student schedule persistence error: {e}")


async def _bg_persist_teacher_schedule(teacher_id: int | str, adapted_data: list[Any]) -> None:
    """Quietly saves fresh teacher schedule into MongoDB TeacherSchedule collection."""
    if not teacher_id or not adapted_data:
        return
    try:
        from src.database.models.teacher_schedule import TeacherSchedule

        num_id = int(teacher_id) if str(teacher_id).isdigit() else teacher_id
        client_id = id(get_client())
        data_json = json.dumps(adapted_data, ensure_ascii=False, sort_keys=True, default=str)
        sch_hash = hashlib.sha256(data_json.encode("utf-8")).hexdigest()
        if _persisted_teacher_hashes.get((client_id, num_id)) == sch_hash:
            return

        now_dt = datetime.now(timezone.utc)
        doc = await TeacherSchedule.find_one({"teacherId": num_id})
        if doc:
            if doc.hash == sch_hash:
                _persisted_teacher_hashes[(client_id, num_id)] = sch_hash
                return
            doc.data = adapted_data
            doc.hash = sch_hash
            doc.checked_at = now_dt
            doc.updated_at = now_dt
            await doc.save()
        else:
            new_doc = TeacherSchedule(
                teacherId=num_id,
                data=adapted_data,
                hash=sch_hash,
                checkedAt=now_dt,
                updatedAt=now_dt,
            )
            await new_doc.insert()
        _persisted_teacher_hashes[(client_id, num_id)] = sch_hash
    except Exception as e:
        logger.debug(f"Background teacher schedule persistence error: {e}")


async def get_or_fetch_student_schedule(
    group_id: int,
    sch_lang: str,
    is_refresh: bool = False,
    group_name: str | None = None,
) -> dict[str, Any] | None:
    """Retrieves student schedule from RAM cache, Buketov API, or MongoDB fallback."""
    norm_lang = _norm_cache_lang(sch_lang)
    cache_key = f"{group_id}_{norm_lang}"
    now = time.time()
    cached = student_schedule_cache.get(cache_key) or student_schedule_cache.get(f"{group_id}_{sch_lang}")

    max_age_sec = 1800.0  # 30 minutes
    needs_fetch = is_refresh or not cached or (now - cached["timestamp"] >= max_age_sec)

    if needs_fetch:
        grp_name = group_name
        if not grp_name:
            meta = _group_meta_cache.get((id(get_client()), group_id))
            if meta and meta.get("name"):
                grp_name = meta["name"]
            else:
                try:
                    from src.database.models.group import Group

                    num_g_id = int(group_id) if str(group_id).isdigit() else group_id
                    g_doc = await Group.find_one(Group.group_id == num_g_id) or await Group.find_one({"id": num_g_id})
                    if g_doc and getattr(g_doc, "name", None):
                        grp_name = g_doc.name
                        _group_meta_cache[(id(get_client()), group_id)] = {
                            "name": grp_name,
                            "back_callback": "faculty|0",
                            "age": getattr(g_doc, "age", None),
                        }
                except Exception:
                    pass

        if grp_name:
            try:
                if is_refresh:
                    raw_data = await buketov_api_service.get_group_schedule(grp_name, force_refresh=True)
                else:
                    raw_data = await buketov_api_service.get_group_schedule(grp_name)
                records = (
                    raw_data.get("records", [])
                    if isinstance(raw_data, dict)
                    else (raw_data if isinstance(raw_data, list) else [])
                )
                adapted = schedule_api_adapter.adapt_group_schedule(records, norm_lang)
                entry_ts = float(raw_data.get("_cacheTimestamp") or now) if isinstance(raw_data, dict) else now
                cached = {
                    "data": adapted,
                    "timestamp": now if is_refresh else entry_ts,
                    "is_from_db": bool(raw_data.get("_fromDb", False)) if isinstance(raw_data, dict) else False,
                }
                student_schedule_cache[cache_key] = cached
                if not cached["is_from_db"] and adapted:
                    asyncio.create_task(_bg_persist_student_schedule(group_id, adapted))
            except Exception as e:
                logger.warning(f"Error fetching schedule for group {grp_name} (id={group_id}): {e}")

        if not cached:
            try:
                from src.database.models.schedule import Schedule

                doc = await Schedule.find_one({"groupId": group_id})
                if doc and doc.data:
                    cached = {
                        "data": [
                            d.model_dump(by_alias=True) if hasattr(d, "model_dump") else d
                            for d in doc.data
                        ],
                        "timestamp": doc.updated_at.timestamp() if doc.updated_at else now,
                        "is_from_db": True,
                    }
                    student_schedule_cache[cache_key] = cached
            except Exception:
                pass
    return cached


async def build_student_day_schedule_view(
    cached: dict[str, Any],
    group_id: int,
    sch_lang: str,
    day_num: int,
    ui_lang: str,
    group_name: str | None = None,
) -> tuple[str, Any]:
    """Builds formatted message text and pagination markup for student schedule."""
    days = cached.get("data", [])
    if not isinstance(days, list) or len(days) <= day_num:
        day_info = {"day": "Понедельник", "subjects": []}
    else:
        day_info = days[day_num]

    days_list = schedule_api_adapter.days_kz if ui_lang in ("kz", "каз") else schedule_api_adapter.days_ru
    day_name = (
        days_list[day_num]
        if (0 <= day_num < len(days_list))
        else (day_info.get("day", "День") if isinstance(day_info, dict) else getattr(day_info, "day", "День"))
    )
    raw_subjects = day_info.get("subjects", []) if isinstance(day_info, dict) else getattr(day_info, "subjects", [])
    active_subjects = [
        s for s in raw_subjects
        if (s.get("subject", "").strip() if isinstance(s, dict) else getattr(s, "subject", "").strip())
    ]

    grp_name = group_name or f"Группа {group_id}"
    back_callback = "faculty|0"
    group_age = None
    client_id = id(get_client())
    cached_meta = _group_meta_cache.get((client_id, group_id))

    if cached_meta is not None:
        if not group_name and cached_meta.get("name"):
            grp_name = cached_meta["name"]
        back_callback = cached_meta.get("back_callback", "faculty|0")
        group_age = cached_meta.get("age")
        if grp_name and group_id:
            register_group_name(group_id, grp_name)
    else:
        g_doc = None
        try:
            from src.database.models.group import Group

            num_g_id = int(group_id) if str(group_id).isdigit() else group_id
            g_doc = await Group.find_one(Group.group_id == num_g_id) or await Group.find_one({"id": num_g_id})
            if not g_doc and group_name:
                g_doc = await Group.find_one(Group.name == group_name) or await Group.find_one({"name": group_name})
            if g_doc:
                if not group_name and getattr(g_doc, "name", None):
                    grp_name = g_doc.name
                if grp_name and group_id:
                    register_group_name(group_id, grp_name)
                group_age = getattr(g_doc, "age", None)
                prog_id = getattr(g_doc, "program", None)
                fac_id = getattr(g_doc, "faculty", None)
                if prog_id:
                    if not fac_id:
                        from src.database.models.program import Program

                        num_p_id = int(prog_id) if str(prog_id).isdigit() else prog_id
                        p_doc = await Program.find_one(Program.program_id == num_p_id) or await Program.find_one({"id": num_p_id})
                        if p_doc and getattr(p_doc, "faculty", None):
                            fac_id = p_doc.faculty
                    fac_id = fac_id or 0
                    back_callback = f"group|{fac_id}|{prog_id}|0"
                elif fac_id:
                    back_callback = f"program|{fac_id}|0"
                _group_meta_cache[(client_id, group_id)] = {
                    "name": getattr(g_doc, "name", None) or grp_name,
                    "back_callback": back_callback,
                    "age": group_age,
                }
        except Exception as e:
            logger.debug(f"Error resolving group schedule back callback: {e}")

    if group_age:
        group_header = (
            f"{t('group_and_year', ui_lang, groupName=grp_name, groupYear=group_age)}\n"
            f"📆 {t('schedule_by_day', ui_lang, dayName=day_name)}\n"
        )
    else:
        group_header = (
            f"👥 <b>{grp_name}</b>\n"
            f"📆 {t('schedule_by_day', ui_lang, dayName=day_name)}\n"
        )

    if not active_subjects:
        body = f"<b>{t('vacation', ui_lang)}</b>\n\n"
    else:
        items = []
        for s in active_subjects:
            s_name = s.get("subject", "").strip() if isinstance(s, dict) else getattr(s, "subject", "").strip()
            s_time = s.get("time", "") if isinstance(s, dict) else getattr(s, "time", "")
            clean_s = s_name if s_name.startswith("📚") else f"📚 {s_name}"
            items.append(f"⌚️ {s_time}\n{clean_s}")
        body = "\n\n".join(items) + "\n\n"

    elapsed = format_elapsed_time(cached["timestamp"], ui_lang, cached.get("is_from_db", False))
    clock = format_timestamp_clock(cached["timestamp"])
    footer = (
        f"🕒 <i><b>{elapsed} || {clock}</b></i>\n"
        f"{t('for_help', ui_lang)}\n"
        f"{t('official_site_compare', ui_lang)}\n"
    )

    full_text = group_header + body + footer

    pre_callback = f"schedule|{sch_lang}|{group_id}"
    markup = get_schedule_pagination_keyboard(pre_callback, day_num, back_callback, ui_lang)
    return full_text, markup


async def render_student_day_schedule_message(
    target_msg: Any,
    group_id: int,
    sch_lang: str,
    day_num: int,
    ui_lang: str,
    is_edit: bool = False,
    group_name: str | None = None,
) -> None:
    """Helper to render student schedule directly into a Message."""
    cached = await get_or_fetch_student_schedule(group_id, sch_lang, group_name=group_name)
    if not cached or not cached.get("data"):
        text = (
            "❌ <b>К сожалению, расписание для данной группы временно недоступно.</b>\n"
            f"{t('official_site_compare', ui_lang)}"
        )
        markup = get_faculties_keyboard([], lang=ui_lang)
        if is_edit:
            await target_msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
        else:
            await target_msg.answer(text, reply_markup=markup, parse_mode="HTML")
        return

    text, markup = await build_student_day_schedule_view(
        cached, group_id, sch_lang, day_num, ui_lang, group_name=group_name
    )
    if is_edit:
        await target_msg.edit_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await target_msg.answer(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)


# === Student Schedule View (with 30m RAM Cache & 10m Refresh Cooldown) ===

@callbacks_router.callback_query(
    F.data.startswith("schedule|")
    | F.data.startswith("refreshschedule|")
    | F.data.startswith("chooseScheduleLanguage|")
    | F.data.startswith("refreshchooseScheduleLanguage|")
)
async def handle_student_schedule_view(call: CallbackQuery) -> None:
    """Renders student schedule by day with ◀️, 🔄, ▶️ pagination and refresh cooldown."""
    data_str = call.data or ""
    is_refresh = data_str.startswith("refresh")
    if is_refresh:
        data_str = data_str.replace("refresh", "", 1)

    parts = data_str.split("|")
    # Formats:
    # chooseScheduleLanguage|<lang>|<group_id>|<day>
    # schedule|<lang>|<group_id>|<day>
    sch_lang = parts[1] if len(parts) > 1 else "ru"
    group_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    day_num = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else get_current_day_number()
    day_num = max(0, min(5, day_num))

    user_id = call.from_user.id if call.from_user else 0
    ui_lang = await user_service.get_user_language(user_id)
    if user_id and group_id:
        asyncio.create_task(user_service.set_user_schedule_preference(user_id, "student", group=group_id))

    norm_lang = _norm_cache_lang(sch_lang)
    cache_key = f"{group_id}_{norm_lang}"
    now = time.time()
    cached = student_schedule_cache.get(cache_key) or student_schedule_cache.get(f"{group_id}_{sch_lang}")

    # 1. Refresh button clicked within cooldown (10 minutes)
    if is_refresh and cached and (now - cached["timestamp"] < COOLDOWN_STUDENT_REFRESH.total_seconds()):
        text, markup = await build_student_day_schedule_view(cached, group_id, sch_lang, day_num, ui_lang)
        if call.message:
            try:
                await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:
                if "message is not modified" not in str(e):
                    logger.debug(f"editMessageText error: {e}")
        await call.answer()
        return

    # 2. Need fresh data (refresh after cooldown or cache older than 30m or missing)
    cached = await get_or_fetch_student_schedule(group_id, sch_lang, is_refresh=is_refresh)

    if not cached or not cached.get("data"):
        await call.answer()
        if call.message:
            await call.message.edit_text(
                "❌ <b>К сожалению, расписание для данной группы временно недоступно.</b>\n"
                f"{t('official_site_compare', ui_lang)}",
                parse_mode="HTML",
                reply_markup=get_faculties_keyboard([], lang=ui_lang),
            )
        return

    text, markup = await build_student_day_schedule_view(cached, group_id, sch_lang, day_num, ui_lang)
    if call.message:
        try:
            await call.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.debug(f"editMessageText error: {e}")

    await call.answer()


# === Department & Teacher Catalog Callbacks ===

@callbacks_router.callback_query(F.data.startswith("department|"))
async def handle_department_menu(call: CallbackQuery) -> None:
    """Displays paginated departments list."""
    await call.answer()
    user_id = call.from_user.id if call.from_user else 0
    lang = await user_service.get_user_language(user_id)

    parts = (call.data or "").split("|")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    try:
        from src.database.models.department import Department

        departments = await Department.find().sort("name").to_list()
        for d in departments:
            d_id = get_domain_id(d, "department_id")
            d_name = _get_attr(d, "name")
            if d_id and d_name:
                register_department_name(d_id, d_name)
    except Exception:
        departments = []

    markup = get_departments_keyboard(departments, page=page, lang=lang)
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(departments) + 9) // 10))
    current_menu_text = f"📌 {t('department_pick', lang)}"
    menu_hint = t("department_pick_hint", lang)
    msg_text = f"{current_menu_text}\n{menu_hint}\n{page_text}"

    if call.message and isinstance(call.message, Message):
        await safe_edit_or_replace_message(call.message, msg_text, reply_markup=markup, parse_mode="HTML")


@callbacks_router.callback_query(F.data.startswith("teacher|"))
async def handle_teacher_menu(call: CallbackQuery) -> None:
    """Displays paginated teachers list for a department."""
    await call.answer()
    user_id = call.from_user.id if call.from_user else 0
    lang = await user_service.get_user_language(user_id)

    parts = (call.data or "").split("|")
    dept_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    if dept_id == 0:
        await handle_department_menu(call)
        return

    try:
        from src.database.models.department import Department
        from src.database.models.teacher import Teacher

        raw_teachers = await Teacher.find({"department": dept_id}).to_list()
        teachers = [
            teacher_directory_service.enrich(raw_t.model_dump(by_alias=True) if hasattr(raw_t, "model_dump") else raw_t)
            for raw_t in raw_teachers
        ]
        teachers = sort_teachers(teachers)
        for tch in teachers:
            tch_id = get_domain_id(tch, "teacher_id")
            tch_name = _get_attr(tch, "fullName") or _get_attr(tch, "name")
            if tch_id and tch_name:
                register_teacher_name(tch_id, tch_name)

        dept = await Department.find_one({"id": dept_id})
        dept_name = dept.name if dept else f"ID {dept_id}"
        if dept_id and dept_name:
            register_department_name(dept_id, dept_name)
    except Exception:
        teachers = []
        dept_name = f"ID {dept_id}"

    curr_day = get_current_day_number()
    markup = get_teachers_keyboard(
        teachers,
        department_id=dept_id,
        page=page,
        lang=lang,
        current_day=curr_day,
    )
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(teachers) + 9) // 10))
    msg_text = f"📌 {t('teacher_pick', lang)}\n📘 {t('department', lang, departmentName=dept_name)}\n{page_text}"

    if call.message and isinstance(call.message, Message):
        await safe_edit_or_replace_message(call.message, msg_text, reply_markup=markup, parse_mode="HTML")


async def get_or_fetch_teacher_text_schedule(
    teacher_id: int | str,
    ui_lang: str,
    is_refresh: bool = False,
    teacher_name: str | None = None,
) -> dict[str, Any] | None:
    """Retrieves teacher schedule from RAM cache, Buketov API, or MongoDB fallback with multi-key aliasing."""
    canonical_id, tch_name, dept_id, lookup_name = await resolve_teacher_identity(teacher_id, teacher_name)
    try:
        from src.cron.cache_warmup import smart_warmup_engine

        smart_warmup_engine.touch_teacher(canonical_id or teacher_id, tch_name or teacher_name)
    except Exception:
        pass
    norm_lang = _norm_cache_lang(ui_lang)

    # Collect all alias keys
    alias_keys = [
        f"{canonical_id}_{norm_lang}",
        f"{teacher_id}_{norm_lang}",
        f"{canonical_id}_{ui_lang}",
        f"{teacher_id}_{ui_lang}",
    ]
    if tch_name:
        alias_keys.append(f"{tch_name}_{norm_lang}")
        clean_name = clean_titles(tch_name)
        if clean_name != tch_name:
            alias_keys.append(f"{clean_name}_{norm_lang}")
    if lookup_name and lookup_name != tch_name:
        alias_keys.append(f"{lookup_name}_{norm_lang}")

    now = time.time()
    cached = None
    for k in alias_keys:
        cached = teacher_text_cache.get(k)
        if cached:
            break

    needs_fetch = is_refresh or not cached or (now - cached["timestamp"] >= 1800.0)
    if not needs_fetch and cached:
        # Propagate cache across all alias keys
        for k in alias_keys:
            if k not in teacher_text_cache:
                teacher_text_cache[k] = cached
        return cached

    if needs_fetch:
        if lookup_name:
            try:
                if is_refresh:
                    raw_sch = await buketov_api_service.get_teacher_schedule(lookup_name, force_refresh=True)
                else:
                    raw_sch = await buketov_api_service.get_teacher_schedule(lookup_name)
                records = (
                    raw_sch.get("records", [])
                    if isinstance(raw_sch, dict)
                    else (raw_sch if isinstance(raw_sch, list) else [])
                )
                adapted = schedule_api_adapter.adapt_teacher_schedule(records, norm_lang)
                entry_ts = float(raw_sch.get("_cacheTimestamp") or now) if isinstance(raw_sch, dict) else now
                cached = {
                    "data": adapted,
                    "timestamp": now if is_refresh else entry_ts,
                    "is_from_db": bool(raw_sch.get("_fromDb", False)) if isinstance(raw_sch, dict) else False,
                }
                for k in alias_keys:
                    teacher_text_cache[k] = cached
                if not cached["is_from_db"] and adapted:
                    asyncio.create_task(_bg_persist_teacher_schedule(canonical_id, adapted))
            except Exception as e:
                logger.warning(f"Error fetching text schedule for teacher {lookup_name} (id={canonical_id}): {e}")

        if not cached:
            try:
                from src.database.models.teacher_schedule import TeacherSchedule

                num_t_id = int(canonical_id) if str(canonical_id).isdigit() else canonical_id
                doc = await TeacherSchedule.find_one({"teacherId": num_t_id})
                if doc and doc.data:
                    cached = {
                        "data": [
                            d.model_dump(by_alias=True) if hasattr(d, "model_dump") else d
                            for d in doc.data
                        ],
                        "timestamp": doc.updated_at.timestamp() if doc.updated_at else now,
                        "is_from_db": True,
                    }
                    for k in alias_keys:
                        teacher_text_cache[k] = cached
            except Exception:
                pass

        if not cached:
            cached = {
                "data": schedule_api_adapter.adapt_teacher_schedule([], norm_lang),
                "timestamp": now,
                "is_from_db": True,
            }
            for k in alias_keys:
                teacher_text_cache[k] = cached

    return cached


async def build_teacher_day_text_view(
    cached: dict[str, Any],
    teacher_id: int | str,
    day_num: int,
    ui_lang: str,
    teacher_name: str | None = None,
) -> tuple[str, Any]:
    """Builds formatted message text and pagination markup for teacher text schedule."""
    days = cached.get("data", [])
    day_info = (
        days[day_num]
        if (isinstance(days, list) and len(days) > day_num)
        else {"day": "Понедельник", "groups": []}
    )
    days_list = schedule_api_adapter.days_kz if ui_lang in ("kz", "каз") else schedule_api_adapter.days_ru
    day_name = (
        days_list[day_num]
        if (0 <= day_num < len(days_list))
        else (day_info.get("day", "День") if isinstance(day_info, dict) else getattr(day_info, "day", "День"))
    )
    slots = day_info.get("groups", []) if isinstance(day_info, dict) else getattr(day_info, "groups", [])
    active_slots = [
        s for s in slots
        if (s.get("group", "").strip() if isinstance(s, dict) else getattr(s, "group", "").strip())
    ]

    # Deduplicate slots by time matching Node.js commit 25601dc
    deduplicated = []
    seen_times: dict[str, dict[str, Any]] = {}
    for s in active_slots:
        item = s if isinstance(s, dict) else (s.model_dump() if hasattr(s, "model_dump") else vars(s))
        raw_time = item.get("time", "")
        norm_t = schedule_api_adapter.normalize_time(raw_time) if hasattr(schedule_api_adapter, "normalize_time") else raw_time
        if norm_t and norm_t in seen_times:
            existing = seen_times[norm_t]
            g = item.get("group", "")
            if g and g not in existing.get("group", ""):
                existing["group"] = f"{existing.get('group', '')}, {g}"
            if not existing.get("subject") and item.get("subject"):
                existing["subject"] = item.get("subject")
                existing["lessonType"] = item.get("lessonType")
        else:
            copy_item = dict(item)
            if norm_t:
                seen_times[norm_t] = copy_item
            deduplicated.append(copy_item)

    canonical_id, tch_name, dept_id, lookup_name = await resolve_teacher_identity(teacher_id, teacher_name)
    client_id = id(get_client())
    cached_meta = _teacher_meta_cache.get((client_id, canonical_id)) or _teacher_meta_cache.get((client_id, teacher_id)) or {}
    job_title = cached_meta.get("job_title", "")
    is_head = bool(cached_meta.get("is_head", False))

    if is_head and not job_title:
        job_title = "Кафедра меңгерушісі" if ui_lang in ("kz", "каз") else "Заведующий кафедрой"

    if teacher_id and tch_name:
        register_teacher_name(teacher_id, tch_name)

    title_part = f" ({job_title})" if job_title else ""
    header = f"👥 <b>{tch_name}</b>{title_part}\n📆 {t('schedule_by_day', ui_lang, dayName=day_name)}\n"

    if not deduplicated:
        body = f"🥳 <b>{t('vacation', ui_lang)}</b>\n\n"
    else:
        formatted_items = []
        for item in deduplicated:
            slot_time = item.get("time", "")
            grp_name = item.get("group", "")
            subj_name = item.get("subject", "")
            l_type = item.get("lessonType", "")

            text = f"⌚️ {slot_time}\n"
            formatted_group = transform_group_string(grp_name)
            text += add_symbol_to_each_line(formatted_group, "👥") + "\n"
            if subj_name:
                formatted_subj = format_subject_with_lesson_type(subj_name, l_type)
                text += f"📖 <i>{formatted_subj}</i>\n"
            formatted_items.append(text.rstrip())
        body = "\n\n".join(formatted_items) + "\n\n"

    elapsed = format_elapsed_time(cached["timestamp"], ui_lang, cached.get("is_from_db", False))
    clock = format_timestamp_clock(cached["timestamp"])
    footer = (
        f"🕰 <i><b>{t('schedule_downloaded', ui_lang, timeAgo=elapsed)} || {clock}</b></i>\n"
        f"{t('for_help', ui_lang)}\n"
        f"{t('official_site_compare', ui_lang)}\n"
    )

    full_text = header + body + footer
    markup = get_teacher_text_keyboard(teacher_id, day_num, department_id=dept_id, lang=ui_lang)
    return full_text, markup


async def render_teacher_day_schedule_message(
    target_msg: Any,
    teacher_id: int | str,
    day_num: int,
    ui_lang: str,
    is_edit: bool = False,
    teacher_name: str | None = None,
) -> None:
    """Helper to render teacher text schedule directly into a Message."""
    try:
        from src.cron.cache_warmup import smart_warmup_engine

        smart_warmup_engine.touch_teacher(teacher_id, teacher_name)
    except Exception:
        pass
    cached = await get_or_fetch_teacher_text_schedule(teacher_id, ui_lang, teacher_name=teacher_name)
    if not cached or not cached.get("data"):
        text = (
            "❌ <b>К сожалению, расписание для преподавателя временно недоступно.</b>\n"
            f"{t('official_site_compare', ui_lang)}"
        )
        markup = get_departments_keyboard([], lang=ui_lang)
        if is_edit:
            await target_msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
        else:
            await target_msg.answer(text, reply_markup=markup, parse_mode="HTML")
        return

    text, markup = await build_teacher_day_text_view(
        cached, teacher_id, day_num, ui_lang, teacher_name=teacher_name
    )
    if is_edit:
        await target_msg.edit_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await target_msg.answer(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)


# === Teacher Schedule Table Image (with 20m Cooldown & Telegram file_id Reuse) ===

@callbacks_router.callback_query(F.data.startswith("teacherImg|") | F.data.startswith("refreshteacherImg|"))
async def handle_teacher_table_image(call: CallbackQuery) -> None:
    """Renders visual PNG schedule table for a teacher or reuses cached file_id."""
    raw_data = call.data or ""
    is_refresh = raw_data.startswith("refreshteacherImg|")
    prefix_len = len("refreshteacherImg|") if is_refresh else len("teacherImg|")
    payload = raw_data[prefix_len:]
    parts = payload.split("|")

    teacher_id = int(parts[0]) if parts[0].isdigit() else parts[0]
    day_num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else get_current_day_number()

    user_id = call.from_user.id if call.from_user else 0
    ui_lang = await user_service.get_user_language(user_id)
    if user_id and teacher_id:
        asyncio.create_task(user_service.set_user_schedule_preference(user_id, "teacher", teacher=teacher_id))
    chat_id = call.message.chat.id if call.message and call.message.chat else user_id

    now = time.time()
    canonical_id, teacher_name, dept_id, lookup_name = await resolve_teacher_identity(teacher_id)
    try:
        from src.cron.cache_warmup import smart_warmup_engine

        smart_warmup_engine.touch_teacher(canonical_id or teacher_id, teacher_name or lookup_name)
    except Exception:
        pass
    t_id_str = str(canonical_id)
    cached = (
        teacher_table_cache.get(t_id_str)
        or teacher_table_cache.get(str(teacher_id))
        or teacher_table_cache.get(teacher_name)
    )

    # 1. 20-minute cooldown check on refresh
    if is_refresh and cached and (now - cached["timestamp"] < COOLDOWN_TEACHER_IMAGE_REFRESH.total_seconds()):
        if call.message and call.message.photo:
            elapsed = format_elapsed_time(cached["timestamp"], ui_lang, cached.get("is_from_db", False))
            clock = format_timestamp_clock(cached["timestamp"])
            caption = (
                f"{t('teacher_grid_caption', ui_lang, teacherName=teacher_name)}\n\n"
                f"🕒 <i><b>{elapsed} || {clock}</b></i>"
            )
            markup = get_teacher_table_keyboard(canonical_id, day_num, department_id=dept_id, lang=ui_lang)
            try:
                await call.message.edit_caption(caption=caption, reply_markup=markup, parse_mode="HTML")
            except Exception:
                pass
        await call.answer()
        return

    # 2. Fetch fresh data if needed
    needs_fetch = is_refresh or not cached or (now - cached["timestamp"] >= 1200.0)
    if needs_fetch:
        await call.answer("⌛️ Создаю таблицу недели...", show_alert=False)
        try:
            resolved_lookup = lookup_name or (teacher_name if not teacher_name.startswith("ID ") else str(teacher_id))
            if is_refresh:
                raw_sch = await buketov_api_service.get_teacher_schedule(resolved_lookup, force_refresh=True)
            else:
                raw_sch = await buketov_api_service.get_teacher_schedule(resolved_lookup)
            records = (
                raw_sch.get("records", [])
                if isinstance(raw_sch, dict)
                else (raw_sch if isinstance(raw_sch, list) else [])
            )
            adapted = schedule_api_adapter.adapt_teacher_schedule(records, ui_lang)
            entry_ts = float(raw_sch.get("_cacheTimestamp") or now) if isinstance(raw_sch, dict) else now
            cached = {
                "data": adapted,
                "timestamp": now,
                "is_from_db": bool(raw_sch.get("_fromDb", False)) if isinstance(raw_sch, dict) else False,
            }
            teacher_table_cache[t_id_str] = cached
            if str(teacher_id) != t_id_str:
                teacher_table_cache[str(teacher_id)] = cached
            if teacher_name:
                teacher_table_cache[teacher_name] = cached
            if not cached["is_from_db"] and adapted:
                asyncio.create_task(_bg_persist_teacher_schedule(teacher_id, adapted))
        except Exception as e:
            logger.warning(f"Error fetching schedule for teacher {teacher_id}: {e}")
            if not cached:
                try:
                    from src.database.models.teacher_schedule import TeacherSchedule

                    num_t_id = int(teacher_id) if str(teacher_id).isdigit() else teacher_id
                    doc = await TeacherSchedule.find_one({"teacherId": num_t_id})
                    if doc and doc.data:
                        cached = {
                            "data": [
                                d.model_dump(by_alias=True) if hasattr(d, "model_dump") else d
                                for d in doc.data
                            ],
                            "timestamp": doc.updated_at.timestamp() if doc.updated_at else now,
                            "is_from_db": True,
                        }
                        teacher_table_cache[t_id_str] = cached
                        if str(teacher_id) != t_id_str:
                            teacher_table_cache[str(teacher_id)] = cached
                        if teacher_name:
                            teacher_table_cache[teacher_name] = cached
                except Exception:
                    pass
            if not cached:
                cached = {
                    "data": schedule_api_adapter.adapt_teacher_schedule([], ui_lang),
                    "timestamp": now,
                    "is_from_db": True,
                }
                teacher_table_cache[t_id_str] = cached
                if str(teacher_id) != t_id_str:
                    teacher_table_cache[str(teacher_id)] = cached
                if teacher_name:
                    teacher_table_cache[teacher_name] = cached
    else:
        await call.answer()

    # Compute hash and check file_id cache
    sch_hash = table_image_service.compute_schedule_hash(cached["data"])
    cached_file_id = table_image_service.get_cached_file_id(teacher_id, ui_lang, sch_hash)

    elapsed = format_elapsed_time(cached["timestamp"], ui_lang, cached.get("is_from_db", False))
    clock = format_timestamp_clock(cached["timestamp"])
    caption = (
        f"{t('teacher_grid_caption', ui_lang, teacherName=teacher_name)}\n\n"
        f"🕒 <i><b>{elapsed} || {clock}</b></i>"
    )
    markup = get_teacher_table_keyboard(teacher_id, day_num, department_id=dept_id, lang=ui_lang)

    bot = call.bot
    if not bot:
        return

    old_message = call.message

    # Try sending via cached Telegram file_id (0 network overhead)
    if cached_file_id and not is_refresh:
        try:
            sent_msg = await bot.send_photo(
                chat_id=chat_id,
                photo=cached_file_id,
                caption=caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
            if old_message:
                try:
                    await old_message.delete()
                except Exception:
                    pass
            return
        except Exception as e:
            logger.debug(f"Sending via file_id failed, falling back to render: {e}")

    # Render PNG bytes
    png_bytes = await table_image_service.render_table_image(
        teacher={"fullName": teacher_name, "id": teacher_id},
        schedule_data=cached["data"],
        lang=ui_lang,
    )

    photo_file = BufferedInputFile(png_bytes, filename=f"teacher_{teacher_id}.png")
    sent_msg = await bot.send_photo(
        chat_id=chat_id,
        photo=photo_file,
        caption=caption,
        reply_markup=markup,
        parse_mode="HTML",
    )

    if old_message:
        try:
            await old_message.delete()
        except Exception:
            pass

    # Save resulting Telegram file_id for reuse
    if sent_msg.photo:
        best_photo = sent_msg.photo[-1]
        table_image_service.cache_file_id(teacher_id, ui_lang, sch_hash, best_photo.file_id)


# === Teacher Text Schedule View (with 5m Cooldown & 30m RAM Cache) ===

@callbacks_router.callback_query(
    F.data.startswith("teacherText|")
    | F.data.startswith("TeacherSchedule|")
    | F.data.startswith("refreshTeacherSchedule|")
)
async def handle_teacher_text_schedule(call: CallbackQuery) -> None:
    """Renders teacher schedule in text format by day."""
    raw_data = call.data or ""
    is_refresh = raw_data.startswith("refreshTeacherSchedule|")
    clean_data = (
        raw_data.replace("refreshTeacherSchedule|", "")
        .replace("TeacherSchedule|", "")
        .replace("teacherText|", "")
    )
    parts = clean_data.split("|")

    teacher_id = int(parts[0]) if parts[0].isdigit() else parts[0]
    day_num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else get_current_day_number()
    day_num = max(0, min(5, day_num))

    user_id = call.from_user.id if call.from_user else 0
    ui_lang = await user_service.get_user_language(user_id)
    canonical_id, tch_name, dept_id, lookup_name = await resolve_teacher_identity(teacher_id)
    try:
        from src.cron.cache_warmup import smart_warmup_engine

        smart_warmup_engine.touch_teacher(canonical_id or teacher_id, tch_name or lookup_name)
    except Exception:
        pass
    if user_id and canonical_id:
        asyncio.create_task(user_service.set_user_schedule_preference(user_id, "teacher", teacher=canonical_id))

    norm_lang = _norm_cache_lang(ui_lang)
    alias_keys = [
        f"{canonical_id}_{norm_lang}",
        f"{teacher_id}_{norm_lang}",
        f"{canonical_id}_{ui_lang}",
        f"{teacher_id}_{ui_lang}",
    ]
    if tch_name:
        alias_keys.append(f"{tch_name}_{norm_lang}")
        clean_name = clean_titles(tch_name)
        if clean_name != tch_name:
            alias_keys.append(f"{clean_name}_{norm_lang}")
    if lookup_name and lookup_name != tch_name:
        alias_keys.append(f"{lookup_name}_{norm_lang}")

    now = time.time()
    cached = None
    for k in alias_keys:
        cached = teacher_text_cache.get(k)
        if cached:
            break

    if is_refresh and cached and (now - cached["timestamp"] < COOLDOWN_TEACHER_TEXT_REFRESH.total_seconds()):
        text, markup = await build_teacher_day_text_view(cached, canonical_id, day_num, ui_lang, teacher_name=tch_name)
        if call.message:
            try:
                await call.message.edit_text(
                    text,
                    reply_markup=markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                if "message is not modified" not in str(e):
                    logger.debug(f"editMessageText error: {e}")
        await call.answer()
        return

    cached = await get_or_fetch_teacher_text_schedule(canonical_id, ui_lang, is_refresh=is_refresh, teacher_name=tch_name)

    text, markup = await build_teacher_day_text_view(cached, canonical_id, day_num, ui_lang, teacher_name=tch_name)
    if call.message:
        if call.message.photo:
            try:
                await call.message.delete()
            except Exception:
                pass
            await call.message.answer(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        else:
            try:
                await call.message.edit_text(
                    text,
                    reply_markup=markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                if "message is not modified" not in str(e):
                    logger.debug(f"editMessageText error: {e}")

    await call.answer()


__all__ = [
    "_bg_persist_student_schedule",
    "_bg_persist_teacher_schedule",
    "build_student_day_schedule_view",
    "build_teacher_day_text_view",
    "callbacks_router",
    "clear_schedule_caches",
    "format_elapsed_time",
    "format_subject_with_lesson_type",
    "format_timestamp_clock",
    "get_current_day_number",
    "get_or_fetch_student_schedule",
    "get_or_fetch_teacher_text_schedule",
    "render_student_day_schedule_message",
    "render_teacher_day_schedule_message",
    "resolve_teacher_identity",
    "safe_edit_or_replace_message",
    "student_schedule_cache",
    "teacher_table_cache",
    "teacher_text_cache",
    "transform_group_string",
]
