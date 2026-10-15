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

from datetime import datetime, timezone
import time
from typing import Any

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from src.bot.keyboards.inline import (
    get_departments_keyboard,
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
from src.core.constants import (
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
from src.services.teacher_directory import teacher_directory_service
from src.services.user_service import user_service

logger = get_logger("callbacks_router")
callbacks_router = Router(name="callbacks_router")

# In-memory RAM caches for schedule viewing
# Key: f"{group_id}_{lang}" -> {"data": [...], "timestamp": float, "group": Any, "is_from_db": bool}
student_schedule_cache: dict[str, dict[str, Any]] = {}

# Key: f"{teacher_id}_{lang}" -> {"data": [...], "timestamp": float, "teacher": Any, "is_from_db": bool}
teacher_text_cache: dict[str, dict[str, Any]] = {}

# Key: str(teacher_id) -> {"data": [...], "timestamp": float, "teacher": Any, "is_from_db": bool}
teacher_table_cache: dict[str, dict[str, Any]] = {}


def clear_schedule_caches() -> None:
    """Clears all in-memory schedule caches (for testing and manual reset)."""
    student_schedule_cache.clear()
    teacher_text_cache.clear()
    teacher_table_cache.clear()


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
        f"<i>{t('new_schedule_menu_content', lang)}</i>\n\n"
        f"💡 {t('new_schedule_menu_hint', lang)}"
    )

    if call.message:
        try:
            await call.message.edit_text(
                prompt,
                reply_markup=get_new_schedule_keyboard(lang),
                parse_mode="HTML",
            )
        except Exception:
            await call.message.answer(
                prompt,
                reply_markup=get_new_schedule_keyboard(lang),
                parse_mode="HTML",
            )


@callbacks_router.callback_query(F.data == "language_select")
async def handle_language_select_menu(call: CallbackQuery) -> None:
    """Opens language selection menu."""
    await call.answer()
    if call.message:
        await call.message.edit_text(
            "Тілді таңдаңыз / Выберите язык интерфейса:",
            reply_markup=get_language_keyboard(),
        )


@callbacks_router.callback_query(F.data.startswith("languageIs"))
async def handle_set_language(call: CallbackQuery) -> None:
    """Changes user preferred language."""
    await call.answer()
    code = (call.data or "").replace("languageIs", "").lower()
    user_id = call.from_user.id if call.from_user else 0
    await user_service.set_user_language(user_id, code)

    lang = "kz" if code == "kz" else "ru"
    ack_text = "Тіл қазақшаға ауыстырылды! 🇰🇿" if lang == "kz" else "Язык переключен на русский! 🇷🇺"

    if call.message:
        await call.message.edit_text(
            f"{ack_text}\n\n<i>{t('new_schedule_menu_content', lang)}</i>",
            reply_markup=get_new_schedule_keyboard(lang),
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
    except Exception:
        faculties = []

    markup = get_faculties_keyboard(faculties, page=page, lang=lang)
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(faculties) + 9) // 10))
    msg_text = f"📍 {t('faculty_pick', lang)}\n{page_text}"

    if call.message:
        await call.message.edit_text(msg_text, reply_markup=markup, parse_mode="HTML")


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
        faculty = await Faculty.find_one({"id": faculty_id})
        faculty_name = faculty.name if faculty else f"ID {faculty_id}"
    except Exception:
        programs = []
        faculty_name = f"ID {faculty_id}"

    markup = get_programs_keyboard(programs, faculty_id=faculty_id, page=page, lang=lang)
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(programs) + 9) // 10))
    msg_text = f"📍 {t('program_pick', lang)}\n🎓 {t('faculty', lang, faculty=faculty_name)}\n{page_text}"

    if call.message:
        await call.message.edit_text(msg_text, reply_markup=markup, parse_mode="HTML")


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

        groups = await Group.find({"program": program_id}).sort("name").to_list()
        program = await Program.find_one({"id": program_id})
        program_name = program.name if program else f"ID {program_id}"
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
    msg_text = f"📍 {t('group_pick', lang)}\n📘 {t('program', lang, program=program_name)}\n{page_text}"

    if call.message:
        await call.message.edit_text(msg_text, reply_markup=markup, parse_mode="HTML")


async def get_or_fetch_student_schedule(
    group_id: int,
    sch_lang: str,
    is_refresh: bool = False,
    group_name: str | None = None,
) -> dict[str, Any] | None:
    """Retrieves student schedule from RAM cache, Buketov API, or MongoDB fallback."""
    cache_key = f"{group_id}_{sch_lang}"
    now = time.time()
    cached = student_schedule_cache.get(cache_key)

    max_age_sec = 1800.0  # 30 minutes
    needs_fetch = is_refresh or not cached or (now - cached["timestamp"] >= max_age_sec)

    if needs_fetch:
        grp_name = group_name
        if not grp_name:
            try:
                from src.database.models.group import Group

                g_doc = await Group.find_one({"id": group_id})
                if g_doc and g_doc.name:
                    grp_name = g_doc.name
            except Exception:
                pass

        if grp_name:
            try:
                raw_data = await buketov_api_service.get_group_schedule(grp_name)
                records = (
                    raw_data.get("records", [])
                    if isinstance(raw_data, dict)
                    else (raw_data if isinstance(raw_data, list) else [])
                )
                adapted = schedule_api_adapter.adapt_group_schedule(records, sch_lang)
                cached = {
                    "data": adapted,
                    "timestamp": now,
                    "is_from_db": bool(raw_data.get("_fromDb", False)) if isinstance(raw_data, dict) else False,
                }
                student_schedule_cache[cache_key] = cached
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

    day_name = day_info.get("day", "День") if isinstance(day_info, dict) else getattr(day_info, "day", "День")
    raw_subjects = day_info.get("subjects", []) if isinstance(day_info, dict) else getattr(day_info, "subjects", [])
    active_subjects = [
        s for s in raw_subjects
        if (s.get("subject", "").strip() if isinstance(s, dict) else getattr(s, "subject", "").strip())
    ]

    grp_name = group_name or f"Группа {group_id}"
    if not group_name:
        try:
            from src.database.models.group import Group

            g_doc = await Group.find_one({"id": group_id})
            if g_doc and g_doc.name:
                grp_name = g_doc.name
        except Exception:
            pass

    group_header = f"👥 <b>{grp_name}</b>\n📆 {t('schedule_by_day', ui_lang, dayName=day_name)}\n\n"

    if not active_subjects:
        body = f"<b>{t('vacation', ui_lang, default='Выходной / Пар нет 🎉')}</b>\n\n"
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
        f"{t('official_site_compare', ui_lang)}"
    )

    full_text = group_header + body + footer

    pre_callback = f"schedule|{sch_lang}|{group_id}"
    back_callback = "faculty|0"
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

    cache_key = f"{group_id}_{sch_lang}"
    now = time.time()
    cached = student_schedule_cache.get(cache_key)

    # 1. Refresh button clicked within cooldown (10 minutes)
    if is_refresh and cached and (now - cached["timestamp"] < COOLDOWN_STUDENT_REFRESH.total_seconds()):
        await call.answer("✅ Расписание актуально", show_alert=False)
        text, markup = await build_student_day_schedule_view(cached, group_id, sch_lang, day_num, ui_lang)
        if call.message:
            try:
                await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:
                if "message is not modified" not in str(e):
                    logger.debug(f"editMessageText error: {e}")
        return

    # 2. Need fresh data (refresh after cooldown or cache older than 30m or missing)
    cached = await get_or_fetch_student_schedule(group_id, sch_lang, is_refresh=is_refresh)
    await call.answer()

    if not cached or not cached.get("data"):
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
    except Exception:
        departments = []

    markup = get_departments_keyboard(departments, page=page, lang=lang)
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(departments) + 9) // 10))
    msg_text = f"📍 {t('department_pick', lang)}\n{page_text}"

    if call.message:
        await call.message.edit_text(msg_text, reply_markup=markup, parse_mode="HTML")


@callbacks_router.callback_query(F.data.startswith("teacher|"))
async def handle_teacher_menu(call: CallbackQuery) -> None:
    """Displays paginated teachers list for a department."""
    await call.answer()
    user_id = call.from_user.id if call.from_user else 0
    lang = await user_service.get_user_language(user_id)

    parts = (call.data or "").split("|")
    dept_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    try:
        from src.database.models.department import Department
        from src.database.models.teacher import Teacher

        teachers = await Teacher.find({"department": dept_id}).sort("name").to_list()
        dept = await Department.find_one({"id": dept_id})
        dept_name = dept.name if dept else f"ID {dept_id}"
    except Exception:
        teachers = []
        dept_name = f"ID {dept_id}"

    markup = get_teachers_keyboard(teachers, department_id=dept_id, page=page, lang=lang)
    page_text = t("page_text", lang, page=page + 1, pageCount=max(1, (len(teachers) + 9) // 10))
    msg_text = f"📍 {t('teacher_pick', lang)}\n🏛 {t('department', lang, departmentName=dept_name)}\n{page_text}"

    if call.message:
        await call.message.edit_text(msg_text, reply_markup=markup, parse_mode="HTML")


async def get_or_fetch_teacher_text_schedule(
    teacher_id: int | str,
    ui_lang: str,
    is_refresh: bool = False,
    teacher_name: str | None = None,
) -> dict[str, Any] | None:
    """Retrieves teacher schedule from RAM cache, Buketov API, or MongoDB fallback."""
    cache_key = f"{teacher_id}_{ui_lang}"
    now = time.time()
    cached = teacher_text_cache.get(cache_key)

    needs_fetch = is_refresh or not cached or (now - cached["timestamp"] >= 1800.0)
    if needs_fetch:
        tch_name = teacher_name
        if not tch_name:
            try:
                from src.database.models.teacher import Teacher

                num_t_id = int(teacher_id) if str(teacher_id).isdigit() else teacher_id
                t_doc = await Teacher.find_one({"id": num_t_id})
                if t_doc and t_doc.name:
                    tch_name = t_doc.name
                elif isinstance(teacher_id, str) and not teacher_id.isdigit():
                    tch_name = teacher_id
            except Exception:
                pass

        if tch_name:
            try:
                raw_sch = await buketov_api_service.get_teacher_schedule(tch_name)
                records = (
                    raw_sch.get("records", [])
                    if isinstance(raw_sch, dict)
                    else (raw_sch if isinstance(raw_sch, list) else [])
                )
                adapted = schedule_api_adapter.adapt_teacher_schedule(records, ui_lang)
                cached = {
                    "data": adapted,
                    "timestamp": now,
                    "is_from_db": bool(raw_sch.get("_fromDb", False)) if isinstance(raw_sch, dict) else False,
                }
                teacher_text_cache[cache_key] = cached
            except Exception as e:
                logger.warning(f"Error fetching text schedule for teacher {tch_name} (id={teacher_id}): {e}")

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
                    teacher_text_cache[cache_key] = cached
            except Exception:
                pass
        if not cached:
            cached = {
                "data": schedule_api_adapter.adapt_teacher_schedule([], ui_lang),
                "timestamp": now,
                "is_from_db": True,
            }
            teacher_text_cache[cache_key] = cached
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
    day_name = day_info.get("day", "День") if isinstance(day_info, dict) else getattr(day_info, "day", "День")
    slots = day_info.get("groups", []) if isinstance(day_info, dict) else getattr(day_info, "groups", [])
    active_slots = [
        s for s in slots
        if (s.get("group", "").strip() if isinstance(s, dict) else getattr(s, "group", "").strip())
    ]

    tch_name = teacher_name or f"ID {teacher_id}"
    if not teacher_name:
        try:
            from src.database.models.teacher import Teacher

            num_id = int(teacher_id) if str(teacher_id).isdigit() else teacher_id
            t_doc = await Teacher.find_one({"id": num_id})
            if t_doc:
                enriched = teacher_directory_service.enrich(t_doc.model_dump(by_alias=True))
                tch_name = enriched.get("fullName") or t_doc.name
        except Exception:
            pass

    header = f"👨‍🏫 <b>{tch_name}</b>\n📆 {t('schedule_by_day', ui_lang, dayName=day_name)}\n\n"

    if not active_slots:
        body = f"<b>{t('vacation', ui_lang, default='Пар нет 🎉')}</b>\n\n"
    else:
        items = []
        for s in active_slots:
            grp_name = s.get("group", "") if isinstance(s, dict) else getattr(s, "group", "")
            subj_name = s.get("subject", "") if isinstance(s, dict) else getattr(s, "subject", "")
            slot_time = s.get("time", "") if isinstance(s, dict) else getattr(s, "time", "")
            grp_line = f"👥 {grp_name}"
            subj_line = f"📚 {subj_name}" if subj_name else ""
            items.append(f"⌚️ {slot_time}\n{grp_line}\n{subj_line}".strip())
        body = "\n\n".join(items) + "\n\n"

    elapsed = format_elapsed_time(cached["timestamp"], ui_lang, cached.get("is_from_db", False))
    clock = format_timestamp_clock(cached["timestamp"])
    footer = (
        f"🕒 <i><b>{elapsed} || {clock}</b></i>\n"
        f"{t('for_help', ui_lang)}\n"
        f"{t('official_site_compare', ui_lang)}"
    )

    full_text = header + body + footer
    markup = get_teacher_text_keyboard(teacher_id, day_num, department_id=0, lang=ui_lang)
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
    chat_id = call.message.chat.id if call.message and call.message.chat else user_id

    now = time.time()
    t_id_str = str(teacher_id)
    cached = teacher_table_cache.get(t_id_str)

    # Teacher profile resolution
    teacher_name = f"ID {teacher_id}"
    t_doc = None
    try:
        from src.database.models.teacher import Teacher

        t_doc = await Teacher.find_one({"id": teacher_id})
        if t_doc:
            enriched = teacher_directory_service.enrich(t_doc.model_dump(by_alias=True))
            teacher_name = enriched.get("fullName") or t_doc.name
    except Exception:
        pass

    # 1. 20-minute cooldown check on refresh
    if is_refresh and cached and (now - cached["timestamp"] < COOLDOWN_TEACHER_IMAGE_REFRESH.total_seconds()):
        await call.answer("✅ Расписание актуально", show_alert=False)
        if call.message and call.message.photo:
            try:
                elapsed = format_elapsed_time(cached["timestamp"], ui_lang, cached.get("is_from_db", False))
                clock = format_timestamp_clock(cached["timestamp"])
                caption = (
                    f"📊 <b>Загруженность: {teacher_name}</b>\n\n"
                    f"🕒 <i><b>{elapsed} || {clock}</b></i>\n"
                    f"{t('for_help', ui_lang)}"
                )
                markup = get_teacher_table_keyboard(teacher_id, day_num, department_id=0, lang=ui_lang)
                await call.message.edit_caption(caption=caption, reply_markup=markup, parse_mode="HTML")
            except Exception:
                pass
        return

    # 2. Fetch fresh data if needed
    needs_fetch = is_refresh or not cached or (now - cached["timestamp"] >= 1200.0)
    if needs_fetch:
        await call.answer("⌛️ Создаю таблицу недели...", show_alert=False)
        try:
            lookup_name = t_doc.name if (t_doc and t_doc.name) else (teacher_name if not teacher_name.startswith("ID ") else str(teacher_id))
            raw_sch = await buketov_api_service.get_teacher_schedule(lookup_name)
            records = (
                raw_sch.get("records", [])
                if isinstance(raw_sch, dict)
                else (raw_sch if isinstance(raw_sch, list) else [])
            )
            adapted = schedule_api_adapter.adapt_teacher_schedule(records, ui_lang)
            cached = {
                "data": adapted,
                "timestamp": now,
                "is_from_db": bool(raw_sch.get("_fromDb", False)) if isinstance(raw_sch, dict) else False,
            }
            teacher_table_cache[t_id_str] = cached
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
                except Exception:
                    pass
            if not cached:
                cached = {
                    "data": schedule_api_adapter.adapt_teacher_schedule([], ui_lang),
                    "timestamp": now,
                    "is_from_db": True,
                }
                teacher_table_cache[t_id_str] = cached
    else:
        await call.answer()

    # Compute hash and check file_id cache
    sch_hash = table_image_service.compute_schedule_hash(cached["data"])
    cached_file_id = table_image_service.get_cached_file_id(teacher_id, ui_lang, sch_hash)

    elapsed = format_elapsed_time(cached["timestamp"], ui_lang, cached.get("is_from_db", False))
    clock = format_timestamp_clock(cached["timestamp"])
    caption = (
        f"📊 <b>Загруженность: {teacher_name}</b>\n\n"
        f"🕒 <i><b>{elapsed} || {clock}</b></i>\n"
        f"{t('for_help', ui_lang)}"
    )
    markup = get_teacher_table_keyboard(teacher_id, day_num, department_id=0, lang=ui_lang)

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
    cache_key = f"{teacher_id}_{ui_lang}"
    now = time.time()
    cached = teacher_text_cache.get(cache_key)

    if is_refresh and cached and (now - cached["timestamp"] < COOLDOWN_TEACHER_TEXT_REFRESH.total_seconds()):
        await call.answer("✅ Расписание актуально", show_alert=False)
        text, markup = await build_teacher_day_text_view(cached, teacher_id, day_num, ui_lang)
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
        return

    cached = await get_or_fetch_teacher_text_schedule(teacher_id, ui_lang, is_refresh=is_refresh)
    await call.answer()

    text, markup = await build_teacher_day_text_view(cached, teacher_id, day_num, ui_lang)
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


__all__ = [
    "build_student_day_schedule_view",
    "build_teacher_day_text_view",
    "callbacks_router",
    "clear_schedule_caches",
    "format_elapsed_time",
    "format_timestamp_clock",
    "get_current_day_number",
    "get_or_fetch_student_schedule",
    "get_or_fetch_teacher_text_schedule",
    "render_student_day_schedule_message",
    "render_teacher_day_schedule_message",
    "student_schedule_cache",
    "teacher_table_cache",
    "teacher_text_cache",
]
