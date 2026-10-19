"""User Action Logging Middleware for aiogram 3.

Records user interactions (commands, text queries, callback queries) into the
MongoDB UserAction collection asynchronously (fire-and-forget background task)
with first interaction tracking and text length capping.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
import sys
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.core.logging import get_logger

logger = get_logger("user_action_logger")


def format_capped_text(raw_text: Any) -> str:
    """Formats and caps log text to max 50 words and 250 characters."""
    if not raw_text:
        return ""
    text = str(raw_text).strip()
    if not text:
        return ""
    words = re.split(r"\s+", text)
    if len(words) > 50:
        text = " ".join(words[:50])
    if len(text) > 250:
        text = text[:250]
    return text


def get_media_category(msg: Message) -> tuple[str, str]:
    """Identifies media type and returns action name and descriptive label."""
    if msg.photo:
        return "media_photo", "[Медиа: фото]"
    if msg.video:
        return "media_video", "[Медиа: видео]"
    if msg.animation:
        return "media_gif", "[Медиа: GIF / анимация]"
    if msg.sticker:
        emoji = f" {msg.sticker.emoji}" if msg.sticker.emoji else ""
        return "media_sticker", f"[Медиа: стикер{emoji}]"
    if msg.voice:
        return "media_voice", "[Медиа: голосовое сообщение]"
    if msg.video_note:
        return "media_video_note", "[Медиа: видеосообщение / кружочек]"
    if msg.audio:
        return "media_audio", "[Медиа: аудиофайл]"
    if msg.document:
        fname = f" {msg.document.file_name}" if msg.document.file_name else ""
        return "media_document", f"[Медиа: документ{fname}]"
    if msg.contact:
        return "media_contact", "[Медиа: контакт]"
    if msg.location:
        return "media_location", "[Медиа: геолокация]"
    if msg.poll:
        return "media_poll", "[Медиа: опрос]"
    if msg.dice:
        emoji_char = getattr(msg.dice, "emoji", "")
        return "media_dice", f"[Медиа: эмодзи/кости {emoji_char}]".strip()
    return "media_other", "[Медиа: файл]"


CALLBACK_DAY_NAMES = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}

DAY_NAME_LOOKUP = {
    "0": "Понедельник", "1": "Вторник", "2": "Среда", "3": "Четверг", "4": "Пятница", "5": "Суббота", "6": "Воскресенье",
    "понедельник": "Понедельник", "дүйсенбі": "Понедельник", "monday": "Понедельник", "mon": "Понедельник", "пн": "Понедельник",
    "вторник": "Вторник", "сейсенбі": "Вторник", "tuesday": "Вторник", "tue": "Вторник", "вт": "Вторник",
    "среда": "Среда", "сәрсенбі": "Среда", "wednesday": "Среда", "wed": "Среда", "ср": "Среда",
    "четверг": "Четверг", "бейсенбі": "Четверг", "thursday": "Четверг", "thu": "Четверг", "чт": "Четверг",
    "пятница": "Пятница", "жұма": "Пятница", "friday": "Пятница", "fri": "Пятница", "пт": "Пятница",
    "суббота": "Суббота", "сенбі": "Суббота", "saturday": "Суббота", "sat": "Суббота", "сб": "Суббота",
    "воскресенье": "Воскресенье", "жексенбі": "Воскресенье", "sunday": "Воскресенье", "sun": "Воскресенье", "вс": "Воскресенье",
}


def resolve_day_name(day_val: Any) -> str:
    """Safely maps day numbers (0..6) or localized day name strings to standard Russian day names."""
    if day_val is None:
        return "Понедельник"
    if isinstance(day_val, int):
        return CALLBACK_DAY_NAMES.get(day_val, f"День {day_val + 1}")
    val_str = str(day_val).strip().lower()
    if val_str in DAY_NAME_LOOKUP:
        return DAY_NAME_LOOKUP[val_str]
    if val_str.isdigit():
        idx = int(val_str)
        return CALLBACK_DAY_NAMES.get(idx, f"День {idx + 1}")
    return str(day_val)

from cachetools import LRUCache, TTLCache

KNOWN_GROUPS: LRUCache[int, str] = LRUCache(maxsize=2000)
KNOWN_TEACHERS: LRUCache[int | str, str] = LRUCache(maxsize=2000)
KNOWN_FACULTIES: LRUCache[int, str] = LRUCache(maxsize=200)
KNOWN_PROGRAMS: LRUCache[int, str] = LRUCache(maxsize=1000)
KNOWN_DEPARTMENTS: LRUCache[int, str] = LRUCache(maxsize=500)


def register_group_name(group_id: int | str | None, name: str | None) -> None:
    """Registers or updates human-readable group name for ID."""
    if group_id is not None and name:
        try:
            KNOWN_GROUPS[int(group_id)] = str(name).strip()
        except Exception:
            pass


def register_teacher_name(teacher_id: int | str | None, name: str | None) -> None:
    """Registers or updates human-readable teacher name for ID."""
    if teacher_id is not None and name:
        try:
            key = int(teacher_id) if str(teacher_id).isdigit() else str(teacher_id).strip()
            KNOWN_TEACHERS[key] = str(name).strip()
        except Exception:
            pass


def register_faculty_name(faculty_id: int | str | None, name: str | None) -> None:
    """Registers or updates human-readable faculty name for ID."""
    if faculty_id is not None and name:
        try:
            KNOWN_FACULTIES[int(faculty_id)] = str(name).strip()
        except Exception:
            pass


def register_program_name(program_id: int | str | None, name: str | None) -> None:
    """Registers or updates human-readable educational program name for ID."""
    if program_id is not None and name:
        try:
            KNOWN_PROGRAMS[int(program_id)] = str(name).strip()
        except Exception:
            pass


def register_department_name(department_id: int | str | None, name: str | None) -> None:
    """Registers or updates human-readable department name for ID."""
    if department_id is not None and name:
        try:
            KNOWN_DEPARTMENTS[int(department_id)] = str(name).strip()
        except Exception:
            pass


def _parse_callback_query(
    raw: str,
    group_map: dict[int, str] | None = None,
    teacher_map: dict[int, str] | None = None,
    faculty_map: dict[int, str] | None = None,
    program_map: dict[int, str] | None = None,
    department_map: dict[int, str] | None = None,
) -> tuple[str, str, int | None, str | None]:
    raw_lower = raw.lower()

    if raw == "start":
        return ("start_menu", "Вернулся на главный экран выбора расписания", None, None)

    if raw == "delete":
        return ("delete_message", "Удалил сообщение бота", None, None)

    if raw == "nothing":
        return ("nothing", "Нажатие на пустой разделитель", None, None)

    if raw == "language_select":
        return ("language_menu", "Открыл меню выбора языка", None, None)

    if raw.startswith("languageIs"):
        code = raw.replace("languageIs", "").strip()
        code_lower = code.lower()
        lang_name = "Русский" if code_lower == "ru" else ("Қазақша" if code_lower == "kz" else code)
        return ("change_language", f"Сменил язык на {lang_name}", None, code)

    if raw.startswith("faculty|"):
        parts = raw.split("|")
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return ("faculty_menu", f"Выбор факультета (стр. {page + 1})", None, None)

    if raw.startswith("program|"):
        parts = raw.split("|")
        fac_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        fac_name = (
            (faculty_map.get(fac_id) if faculty_map and fac_id in faculty_map else KNOWN_FACULTIES.get(fac_id))
            if fac_id is not None
            else None
        )
        fac_str = f'факультет "{fac_name}"' if fac_name else (f"факультет ID {fac_id}" if fac_id else "факультет")
        return ("program_menu", f"Выбор специальности ({fac_str}, стр. {page + 1})", fac_id, fac_name)

    if raw.startswith("group|"):
        parts = raw.split("|")
        fac_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        prog_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        prog_name = (
            (program_map.get(prog_id) if program_map and prog_id in program_map else KNOWN_PROGRAMS.get(prog_id))
            if prog_id is not None
            else None
        )
        prog_str = (
            f'специальность "{prog_name}"'
            if prog_name
            else (f"специальность ID {prog_id}" if prog_id else "специальность")
        )
        return ("group_menu", f"Выбор группы ({prog_str}, стр. {page + 1})", prog_id, prog_name)

    if (
        raw_lower.startswith("refreshschedule|")
        or raw_lower.startswith("refresh_schedule|")
        or raw_lower.startswith("refreshchooseschedulelanguage|")
        or raw_lower.startswith("refresh_chooseschedulelanguage|")
    ):
        m = re.match(r"^refresh(?:_)?(?:schedule|chooseschedulelanguage)\|", raw, re.IGNORECASE)
        prefix_len = len(m.group(0)) if m else raw.find("|") + 1
        clean = raw[prefix_len:]
        parts = [p for p in clean.split("|") if p != ""]
        lang_val = ""
        g_id = None
        day_val: Any = 0
        if len(parts) >= 2 and parts[0].isdigit():
            g_id = int(parts[0])
            day_val = parts[1]
        elif len(parts) >= 3 and parts[1].isdigit():
            lang_val = parts[0]
            g_id = int(parts[1])
            day_val = parts[2]
        elif len(parts) == 1 and parts[0].isdigit():
            g_id = int(parts[0])

        day_name = resolve_day_name(day_val)
        g_name = (
            (group_map.get(g_id) if group_map and g_id in group_map else KNOWN_GROUPS.get(g_id))
            if g_id is not None
            else None
        )
        grp_label = f'"{g_name}"' if g_name else (f"ID {g_id}" if g_id else "")
        desc = f"Обновил расписание группы {grp_label} ({day_name})" if grp_label else f"Обновил расписание ({day_name})"
        return ("refresh_group", desc, g_id, g_name)

    if raw.startswith("chooseScheduleLanguage|"):
        parts = [p for p in raw.split("|")[1:] if p != ""]
        lang_val = ""
        g_id = None
        day_val: Any = 0
        if len(parts) >= 2 and parts[0].isdigit():
            g_id = int(parts[0])
            day_val = parts[1]
        elif len(parts) >= 3 and parts[1].isdigit():
            lang_val = parts[0]
            g_id = int(parts[1])
            day_val = parts[2]
        elif len(parts) == 1 and parts[0].isdigit():
            g_id = int(parts[0])

        g_name = (
            (group_map.get(g_id) if group_map and g_id in group_map else KNOWN_GROUPS.get(g_id))
            if g_id is not None
            else None
        )
        grp_label = f'"{g_name}"' if g_name else (f"ID {g_id}" if g_id else "")
        desc = f"Выбор языка расписания (группа {grp_label})" if grp_label else "Выбор языка расписания"
        return ("choose_schedule_language", desc, g_id, g_name or lang_val or None)

    if raw.startswith("schedule|"):
        parts = [p for p in raw.split("|")[1:] if p != ""]
        lang_val = ""
        g_id = None
        day_val: Any = 0
        if len(parts) >= 2 and parts[0].isdigit():
            g_id = int(parts[0])
            day_val = parts[1]
        elif len(parts) >= 3 and parts[1].isdigit():
            lang_val = parts[0]
            g_id = int(parts[1])
            day_val = parts[2]
        elif len(parts) == 1 and parts[0].isdigit():
            g_id = int(parts[0])

        day_name = resolve_day_name(day_val)
        g_name = (
            (group_map.get(g_id) if group_map and g_id in group_map else KNOWN_GROUPS.get(g_id))
            if g_id is not None
            else None
        )
        grp_label = f'"{g_name}"' if g_name else (f"ID {g_id}" if g_id else "")
        desc = f"Открыл расписание группы {grp_label} ({day_name})" if grp_label else f"Открыл расписание ({day_name})"
        return ("view_group", desc, g_id, g_name)

    if raw.startswith("department|"):
        parts = raw.split("|")
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return ("department_menu", f"Выбор кафедры (стр. {page + 1})", None, None)

    if raw.startswith("teacher|"):
        parts = raw.split("|")
        dept_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        dept_name = (
            (
                department_map.get(dept_id)
                if department_map and dept_id in department_map
                else KNOWN_DEPARTMENTS.get(dept_id)
            )
            if dept_id is not None
            else None
        )
        dept_str = f'кафедра "{dept_name}"' if dept_name else (f"кафедра ID {dept_id}" if dept_id else "кафедра")
        return ("teacher_menu", f"Выбор преподавателя ({dept_str}, стр. {page + 1})", dept_id, dept_name)

    if raw_lower.startswith("refreshteacherschedule|") or raw_lower.startswith("refresh_teacherschedule|"):
        parts = raw.split("|")
        raw_t = parts[1].strip() if len(parts) > 1 else ""
        t_id = int(raw_t) if raw_t.isdigit() else None
        day_val = parts[2] if len(parts) > 2 else 0
        day_name = resolve_day_name(day_val)
        lookup_key: Any = t_id if t_id is not None else (raw_t or None)
        t_name = (
            (teacher_map.get(lookup_key) if teacher_map and lookup_key in teacher_map else KNOWN_TEACHERS.get(lookup_key))
            if lookup_key is not None
            else None
        ) or (raw_t if (raw_t and t_id is None) else None)
        tch_label = f'"{t_name}"' if t_name else (f"ID {t_id}" if t_id else "")
        desc = f"Обновил расписание преподавателя {tch_label} ({day_name})" if tch_label else f"Обновил расписание преподавателя ({day_name})"
        return ("refresh_teacher", desc, t_id, t_name)

    if raw_lower.startswith("teacherschedule|"):
        parts = raw.split("|")
        raw_t = parts[1].strip() if len(parts) > 1 else ""
        t_id = int(raw_t) if raw_t.isdigit() else None
        day_val = parts[2] if len(parts) > 2 else 0
        day_name = resolve_day_name(day_val)
        lookup_key = t_id if t_id is not None else (raw_t or None)
        t_name = (
            (teacher_map.get(lookup_key) if teacher_map and lookup_key in teacher_map else KNOWN_TEACHERS.get(lookup_key))
            if lookup_key is not None
            else None
        ) or (raw_t if (raw_t and t_id is None) else None)
        tch_label = f'"{t_name}"' if t_name else (f"ID {t_id}" if t_id else "")
        desc = f"Открыл расписание преподавателя {tch_label} ({day_name})" if tch_label else f"Открыл расписание преподавателя ({day_name})"
        return ("view_teacher", desc, t_id, t_name)

    if raw_lower.startswith("refreshteacherimg|") or raw_lower.startswith("refresh_teacherimg|"):
        parts = raw.split("|")
        raw_t = parts[1].strip() if len(parts) > 1 else ""
        t_id = int(raw_t) if raw_t.isdigit() else None
        lookup_key = t_id if t_id is not None else (raw_t or None)
        t_name = (
            (teacher_map.get(lookup_key) if teacher_map and lookup_key in teacher_map else KNOWN_TEACHERS.get(lookup_key))
            if lookup_key is not None
            else None
        ) or (raw_t if (raw_t and t_id is None) else None)
        tch_label = f'"{t_name}"' if t_name else (f"ID {t_id}" if t_id else "")
        desc = f"Обновил таблицу недели преподавателя {tch_label}" if tch_label else "Обновил таблицу недели преподавателя"
        return ("refresh_teacher_image", desc, t_id, t_name)

    if raw_lower.startswith("teacherimg|"):
        parts = raw.split("|")
        raw_t = parts[1].strip() if len(parts) > 1 else ""
        t_id = int(raw_t) if raw_t.isdigit() else None
        lookup_key = t_id if t_id is not None else (raw_t or None)
        t_name = (
            (teacher_map.get(lookup_key) if teacher_map and lookup_key in teacher_map else KNOWN_TEACHERS.get(lookup_key))
            if lookup_key is not None
            else None
        ) or (raw_t if (raw_t and t_id is None) else None)
        tch_label = f'"{t_name}"' if t_name else (f"ID {t_id}" if t_id else "")
        desc = f"Открыл таблицу недели преподавателя {tch_label}" if tch_label else "Открыл таблицу недели преподавателя"
        return ("view_teacher_image", desc, t_id, t_name)

    if raw_lower.startswith("teachertext|"):
        parts = raw.split("|")
        raw_t = parts[1].strip() if len(parts) > 1 else ""
        t_id = int(raw_t) if raw_t.isdigit() else None
        day_val = parts[2] if len(parts) > 2 else 0
        day_name = resolve_day_name(day_val)
        lookup_key = t_id if t_id is not None else (raw_t or None)
        t_name = (
            (teacher_map.get(lookup_key) if teacher_map and lookup_key in teacher_map else KNOWN_TEACHERS.get(lookup_key))
            if lookup_key is not None
            else None
        ) or (raw_t if (raw_t and t_id is None) else None)
        tch_label = f'"{t_name}"' if t_name else (f"ID {t_id}" if t_id else "")
        desc = f"Переключился на текстовый вид преподавателя {tch_label} ({day_name})" if tch_label else f"Переключился на текстовый вид ({day_name})"
        return ("view_teacher_text", desc, t_id, t_name)

    if raw.startswith("profile|"):
        parts = raw.split("|")
        p_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        p_name = (
            (teacher_map.get(p_id) if teacher_map and p_id in teacher_map else KNOWN_TEACHERS.get(p_id))
            if p_id is not None
            else None
        )
        p_label = f'"{p_name}"' if p_name else (f"ID {p_id}" if p_id else "")
        desc = f"Открыл профиль преподавателя {p_label}" if p_label else "Открыл профиль преподавателя"
        return ("view_profile", desc, p_id, p_name)

    if raw.startswith("searchGroup|"):
        parts = raw.split("|")
        name = parts[1] if len(parts) > 1 else ""
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        return ("search_group_page", f'Пагинация поиска групп: "{name}" (стр. {page + 1})', None, name)

    if raw.startswith("searchTeacher|"):
        parts = raw.split("|")
        name = parts[1] if len(parts) > 1 else ""
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        return ("search_teacher_page", f'Пагинация поиска преподавателей: "{name}" (стр. {page + 1})', None, name)

    return ("button_click", raw, None, None)


def describe_callback_query(
    call_data: str,
    group_map: dict[int, str] | None = None,
    teacher_map: dict[int, str] | None = None,
    faculty_map: dict[int, str] | None = None,
    program_map: dict[int, str] | None = None,
    department_map: dict[int, str] | None = None,
    schedule_badge: str | None = None,
) -> tuple[str, str, int | None, str | None]:
    """Translates technical callback string into a clear human-readable Russian description.

    Returns:
        tuple of (action_code, descriptive_text, entity_id, entity_name)
    """
    if not call_data:
        return ("button_click", "Нажатие кнопки", None, None)

    raw = str(call_data).strip()

    # Extract existing badge if present at the start: e.g. "[🟢] schedule|..." or "[🟢] Открыл..."
    extracted_badge = schedule_badge
    match_badge = re.match(r"^\[([🟢🟣🔵🔴])\]\s*", raw)
    if match_badge:
        if not extracted_badge:
            extracted_badge = match_badge.group(1)
        raw = raw[match_badge.end():].strip()

    # If already a human-readable action description, return with badge preserved
    if (
        raw.startswith("[")
        or raw.startswith("🆕")
        or raw.startswith("Открыл")
        or raw.startswith("Обновил")
        or raw.startswith("Выбор")
        or raw.startswith("Сменил")
        or raw.startswith("Удалил")
        or raw.startswith("Вернулся")
        or raw.startswith("Переключился")
        or raw.startswith("Поиск")
    ):
        full_text = f"[{extracted_badge}] {raw}" if extracted_badge and not raw.startswith(f"[{extracted_badge}]") else raw
        return ("button_click", full_text, None, None)

    action, desc, ent_id, ent_name = _parse_callback_query(
        raw,
        group_map=group_map,
        teacher_map=teacher_map,
        faculty_map=faculty_map,
        program_map=program_map,
        department_map=department_map,
    )

    if extracted_badge and not desc.startswith(f"[{extracted_badge}]"):
        desc = f"[{extracted_badge}] {desc}"

    return (action, desc, ent_id, ent_name)


class _BoundedUserSet(TTLCache[int, bool]):
    """Bounded TTL set compatible with set.add() and 'in' operator."""

    def add(self, item: int) -> None:
        self[item] = True


class UserActionLoggingMiddleware(BaseMiddleware):
    """Asynchronously logs user interactions into the UserAction database."""

    def __init__(self) -> None:
        super().__init__()
        self._known_active_users: _BoundedUserSet = _BoundedUserSet(maxsize=10000, ttl=86400.0)
        self._known_button_users: _BoundedUserSet = _BoundedUserSet(maxsize=10000, ttl=86400.0)
        self._queue: list[dict[str, Any]] = []
        self._batch_threshold = 25
        self._max_queue_size = 2000
        self._is_flushing = False
        self._flush_lock = asyncio.Lock()
        self._log_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._bg_log_tasks: set[asyncio.Task[Any]] = set()
        self.debounce_interval: float = 0.02 if "pytest" in sys.modules else 1.5

    def clear_cache(self) -> None:
        """Clears in-memory active user sets and queue."""
        self._known_active_users.clear()
        self._known_button_users.clear()
        self._queue.clear()
        for t_item in list(self._bg_log_tasks):
            if not t_item.done():
                t_item.cancel()
        self._bg_log_tasks.clear()
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None

    async def flush(self) -> None:
        """Flushes enqueued action records to MongoDB."""
        current = asyncio.current_task()
        pending = [t_item for t_item in list(self._bg_log_tasks) if not t_item.done() and t_item is not current]
        self._bg_log_tasks.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        async with self._flush_lock:
            if not self._queue:
                return
            items_to_save = self._queue[:]
            self._queue.clear()

        try:
            from src.database.models.user_action import UserAction

            docs = [
                UserAction(
                    userId=r["user_id"],
                    username=r["username"],
                    action=r["action"],
                    text=r["text"],
                    isFirst=r["is_first"],
                    entityId=r.get("entity_id"),
                    entityName=r.get("entity_name"),
                    createdAt=r.get("created_at") or datetime.now(timezone.utc),
                )
                for r in items_to_save
            ]
            await UserAction.insert_many(docs)
        except Exception as e:
            logger.debug(f"User action batch flush error: {e}")
            # Fallback: try individual inserts
            for r in items_to_save:
                try:
                    from src.database.models.user_action import UserAction

                    action_doc = UserAction(
                        userId=r["user_id"],
                        username=r["username"],
                        action=r["action"],
                        text=r["text"],
                        isFirst=r["is_first"],
                        entityId=r.get("entity_id"),
                        entityName=r.get("entity_name"),
                        createdAt=r.get("created_at") or datetime.now(timezone.utc),
                    )
                    await action_doc.insert()
                except Exception:
                    pass

    async def _save_record(self, record: dict[str, Any]) -> None:
        """Enqueues record and triggers debounced background flush."""
        self._queue.append(record)
        if len(self._queue) > self._max_queue_size:
            # Drop oldest records to prevent unbounded RAM growth
            self._queue.pop(0)

        if len(self._queue) >= self._batch_threshold:
            self._flush_task = asyncio.create_task(self.flush())
        elif self._flush_task is None or self._flush_task.done():
            async def _debounced() -> None:
                try:
                    await asyncio.sleep(self.debounce_interval)
                except asyncio.CancelledError:
                    return
                await asyncio.shield(self.flush())

            self._flush_task = asyncio.create_task(_debounced())

    async def _check_has_prior_action(self, user_id: int) -> bool:
        """Checks whether user has performed any prior interaction in queue or DB."""
        if any(r["user_id"] == user_id for r in self._queue):
            return True
        try:
            from src.database.models.user_action import UserAction

            prior = await UserAction.find_one({"userId": user_id})
            return prior is not None
        except Exception:
            return False

    async def _check_has_prior_button(self, user_id: int) -> bool:
        """Checks whether user has clicked any prior button in queue or DB."""
        is_btn = lambda a: (
            a not in ("command", "chat_input", "first_message", "long_text_rejected", "spam_blocked")
            and not a.startswith("media_")
            and not a.startswith("search_")
        )
        if any(r["user_id"] == user_id and is_btn(r["action"]) for r in self._queue):
            return True
        try:
            from beanie.operators import NotIn
            from src.database.models.user_action import UserAction

            prior_btn = await UserAction.find_one(
                UserAction.user_id == user_id,
                NotIn(
                    UserAction.action,
                    [
                        "command",
                        "chat_input",
                        "first_message",
                        "long_text_rejected",
                        "spam_blocked",
                        "media_rejected",
                        "media_photo",
                        "media_gif",
                        "media_video",
                        "media_voice",
                        "media_video_note",
                        "media_audio",
                        "media_document",
                        "media_sticker",
                        "media_contact",
                        "media_location",
                        "media_poll",
                        "media_dice",
                        "media_other",
                    ],
                ),
            )
            return prior_btn is not None
        except Exception:
            return False

    async def _process_log_action(
        self,
        user_id: int,
        clean_username: str | None,
        safe_action: str,
        raw_text: str,
        is_button_action: bool,
        entity_id: int | None,
        entity_name: str | None,
        created_at: datetime,
    ) -> None:
        """Serializes first-time user DB checks and enqueues record without blocking handler."""
        async with self._log_lock:
            is_first_action = False
            is_first_button = False

            # First action check
            if user_id not in self._known_active_users:
                has_prior = await self._check_has_prior_action(user_id)
                if not has_prior:
                    is_first_action = True
                self._known_active_users.add(user_id)

            # First button click check
            if is_button_action and user_id not in self._known_button_users:
                has_prior_button = await self._check_has_prior_button(user_id)
                if not has_prior_button:
                    is_first_button = True
                self._known_button_users.add(user_id)

            is_first = False
            safe_text = raw_text

            if is_first_action:
                is_first = True
                if is_button_action:
                    safe_text = f"🆕 [Первое действие пользователя (кнопка)] {raw_text}"
                else:
                    safe_text = f"🆕 [Первое сообщение пользователя] {raw_text}"
            elif is_first_button:
                is_first = True
                safe_text = f"🆕 [Первое нажатие кнопки] {raw_text}"

            record = {
                "user_id": user_id,
                "username": clean_username,
                "action": safe_action,
                "text": safe_text,
                "is_first": is_first,
                "entity_id": entity_id,
                "entity_name": entity_name,
                "created_at": created_at,
            }

            await self._save_record(record)

    async def log_action(
        self,
        user_id: int,
        username: str | None,
        action: str,
        text: str,
        entity_id: int | None = None,
        entity_name: str | None = None,
    ) -> None:
        """Logs an action asynchronously (fire-and-forget when DB check is needed)."""
        if not user_id:
            return

        clean_username = username.lstrip("@") if username else None
        safe_action = (action or "action").strip()
        raw_text = format_capped_text(text) or safe_action
        now_dt = datetime.now(timezone.utc)

        NON_BUTTON_ACTIONS = (
            "command",
            "chat_input",
            "first_message",
            "long_text_rejected",
            "spam_blocked",
        )
        is_button_action = (
            safe_action not in NON_BUTTON_ACTIONS
            and not safe_action.startswith("media_")
            and not safe_action.startswith("search_")
        )

        # Fast path: user is already known in RAM and no background log tasks are pending -> 0ms
        if (
            not self._bg_log_tasks
            and user_id in self._known_active_users
            and (not is_button_action or user_id in self._known_button_users)
        ):
            record = {
                "user_id": user_id,
                "username": clean_username,
                "action": safe_action,
                "text": raw_text,
                "is_first": False,
                "entity_id": entity_id,
                "entity_name": entity_name,
                "created_at": now_dt,
            }
            await self._save_record(record)
            return

        # Slow path (first action/button check in DB): run in background task so handler executes in 0ms
        bg_task = asyncio.create_task(
            self._process_log_action(
                user_id=user_id,
                clean_username=clean_username,
                safe_action=safe_action,
                raw_text=raw_text,
                is_button_action=is_button_action,
                entity_id=entity_id,
                entity_name=entity_name,
                created_at=now_dt,
            )
        )
        self._bg_log_tasks.add(bg_task)
        bg_task.add_done_callback(self._bg_log_tasks.discard)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if not user or not user.id:
            return await handler(event, data)

        user_id = user.id
        username = user.username

        if isinstance(event, Message):
            is_private = (event.chat.type == "private") if (hasattr(event, "chat") and event.chat) else True
            is_cmd = bool(
                event.text
                and (
                    event.text.startswith("/")
                    or any(event.text.startswith(p) for p in ("🗒", "🗓", "❓", "💡", "Г ", "Т ", "П ", "О "))
                )
            )

            # In group chats/conversations, only log explicit bot commands and menu clicks.
            # Casual chatter between group members is strictly ignored.
            if not is_private and not is_cmd:
                return await handler(event, data)

            if event.text:
                if event.text.startswith("/"):
                    action = "command"
                elif any(event.text.startswith(p) for p in ("🗒", "🗓", "❓", "💡", "Г ", "Т ", "П ", "О ")):
                    action = "menu_button"
                else:
                    action = "chat_input"
                text = event.text
            else:
                action, text = get_media_category(event)
                if event.caption:
                    text += f' "{event.caption}"'

            await self.log_action(user_id, username, action, text)

        elif isinstance(event, CallbackQuery):
            call_data = event.data or "callback"
            if call_data == "nothing":
                return await handler(event, data)

            data_holder: dict[str, Any] = {}
            data["data"] = data_holder
            try:
                result = await handler(event, data)
            finally:
                data.pop("data", None)
                try:
                    badge = data.get("schedule_badge") or data_holder.get("schedule_badge")
                    action, desc, entity_id, entity_name = describe_callback_query(call_data, schedule_badge=badge)
                    await self.log_action(
                        user_id,
                        username,
                        action,
                        desc,
                        entity_id=entity_id,
                        entity_name=entity_name,
                    )
                except Exception as log_err:
                    logger.debug(f"User action logging error in callback: {log_err}")
            return result

        return await handler(event, data)


# Global singleton instance
user_action_logging_middleware = UserActionLoggingMiddleware()

__all__ = [
    "CALLBACK_DAY_NAMES",
    "DAY_NAME_LOOKUP",
    "KNOWN_DEPARTMENTS",
    "KNOWN_FACULTIES",
    "KNOWN_GROUPS",
    "KNOWN_PROGRAMS",
    "KNOWN_TEACHERS",
    "UserActionLoggingMiddleware",
    "describe_callback_query",
    "format_capped_text",
    "get_media_category",
    "register_department_name",
    "register_faculty_name",
    "register_group_name",
    "register_program_name",
    "register_teacher_name",
    "resolve_day_name",
    "user_action_logging_middleware",
]
