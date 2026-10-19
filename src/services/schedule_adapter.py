"""Schedule API Adapter for Buketov KarU.

Converts raw REST API responses into normalized daily schedules
for student groups and teachers, grouping subgroups with '📚'
and localizing days of the week into Russian or Kazakh.
"""

from __future__ import annotations

import re
from typing import Any

from src.services.teacher_directory import teacher_directory_service


_ROOM_PREFIX_RE = re.compile(r"^(?:Ауд(?:итория)?\.?)\s*", flags=re.IGNORECASE)
_BLD_PREFIX_RE = re.compile(r"^(?:Корп(?:ус)?\.?|Ғимарат(?:ы)?\.?)\s*", flags=re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_DASH_RE = re.compile(r"[-–—]")
_LEADING_ZERO_RE = re.compile(r"(^|-)0(\d)")
_PAREN_RE = re.compile(r"\s*\([^)]*\)")


class ScheduleApiAdapter:
    """Transforms raw university API records into bot schedule formats."""

    def __init__(self) -> None:
        self.days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
        self.days_kz = ["Дүйсенбі", "Сейсенбі", "Сәрсенбі", "Бейсенбі", "Жұма", "Сенбі"]
        self.day_ru_to_kz = dict(zip(self.days_ru, self.days_kz))

    def get_day_index(self, record: dict[str, Any]) -> int:
        """Determines 1-based day index (1=Mon ... 7=Sun) from record."""
        day_idx = (
            record.get("dayIndex")
            if record.get("dayIndex") is not None
            else (
                record.get("day_index")
                if record.get("day_index") is not None
                else (
                    record.get("dayOfWeek")
                    if record.get("dayOfWeek") is not None
                    else record.get("day_of_week")
                )
            )
        )
        if day_idx is not None:
            try:
                idx = int(day_idx)
                if 1 <= idx <= 7:
                    return idx
            except (ValueError, TypeError):
                pass

        raw_day = str(record.get("day") or "").strip().lower()
        day_map = {
            "понедельник": 1,
            "дүйсенбі": 1,
            "monday": 1,
            "вторник": 2,
            "сейсенбі": 2,
            "tuesday": 2,
            "среда": 3,
            "сәрсенбі": 3,
            "wednesday": 3,
            "четверг": 4,
            "бейсенбі": 4,
            "thursday": 4,
            "пятница": 5,
            "жұма": 5,
            "friday": 5,
            "суббота": 6,
            "сенбі": 6,
            "saturday": 6,
            "воскресенье": 7,
            "жексенбі": 7,
            "sunday": 7,
        }
        return day_map.get(raw_day, 0)

    def normalize_time(self, t: str | None) -> str:
        """Normalizes time string (e.g., '08:30-09:20' -> '8.30-9.20')."""
        if not t:
            return ""
        s = _WS_RE.sub("", str(t))
        s = s.replace(":", ".")
        s = _DASH_RE.sub("-", s)
        s = _LEADING_ZERO_RE.sub(r"\1\2", s)
        return s

    def format_lesson_type(self, lesson_type: str | None) -> str:
        """Formats lesson type badge with slashes (e.g. '/Лекция/')."""
        if not lesson_type:
            return ""
        clean = str(lesson_type).strip().strip("/")
        lower = clean.lower()
        if "лекц" in lower or lower == "лек" or "дәріс" in lower or "дарис" in lower:
            return "/Лекция/"
        if "прак" in lower or "семин" in lower or lower == "пр" or "тәжірибе" in lower or "тажирибе" in lower:
            return "/Прак.зан./"
        if "лаб" in lower or "зертхана" in lower:
            return "/Лаб.раб./"
        if "сроп" in lower or "соөж" in lower or "соож" in lower:
            return "/СРОП/"
        return f"/{clean}/"

    def format_lesson_type_badge(self, lesson_type: str | None) -> str:
        """Formats plain text lesson type (e.g. 'Лекция')."""
        if not lesson_type:
            return ""
        clean = str(lesson_type).strip().strip("/")
        lower = clean.lower()
        if "лекц" in lower or lower == "лек" or "дәріс" in lower or "дарис" in lower:
            return "Лекция"
        if "прак" in lower or "семин" in lower or lower == "пр" or "тәжірибе" in lower or "тажирибе" in lower:
            return "Прак.зан."
        if "лаб" in lower or "зертхана" in lower:
            return "Лаб.раб."
        if "сроп" in lower or "соөж" in lower or "соож" in lower:
            return "СРОП"
        return clean

    def format_room(self, room: str | None, building: str | None) -> str:
        """Formats classroom and university building string."""
        if not room and not building:
            return ""
        clean_room = _ROOM_PREFIX_RE.sub("", str(room or "")).strip()
        clean_building = _BLD_PREFIX_RE.sub("", str(building or "")).strip()
        if clean_room and clean_building:
            if clean_room.endswith(f"/{clean_building}"):
                return f"Ауд.{clean_room}"
            return f"Ауд.{clean_room}/{clean_building}"
        return f"Ауд.{clean_room or clean_building}"

    def get_kz_from_ru_teacher_schedule(
        self, ru_schedule: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Maps an already adapted Russian teacher schedule to Kazakh in O(N)."""
        return [
            {
                **d,
                "day": self.day_ru_to_kz.get(d.get("day", ""), d.get("day", "")),
            }
            for d in ru_schedule
        ]

    def localize_schedule_days(
        self,
        days_data: list[Any] | None,
        language: str = "рус",
    ) -> list[dict[str, Any]]:
        """Translates day names in an adapted schedule to Russian or Kazakh."""
        if not days_data:
            return []
        norm_lang = str(language or "").lower().strip()
        is_kz = norm_lang in ("каз", "kz", "kaz", "қаз")
        target_days = self.days_kz if is_kz else self.days_ru

        localized: list[dict[str, Any]] = []
        for d in days_data:
            d_dict = (
                d if isinstance(d, dict)
                else (d.model_dump() if hasattr(d, "model_dump") else getattr(d, "__dict__", {}))
            )
            d_copy = dict(d_dict)
            day_idx = self.get_day_index(d_copy)
            if 1 <= day_idx <= len(target_days):
                d_copy["day"] = target_days[day_idx - 1]
            localized.append(d_copy)
        return localized

    def adapt_group_schedule(
        self,
        records: list[dict[str, Any]] | None = None,
        language: str = "рус",
    ) -> list[dict[str, Any]]:
        """Adapts raw Buketov API records into student group schedule."""
        records = records or []
        norm_lang = str(language or "").lower().strip()
        is_kz = norm_lang in ("каз", "kz", "kaz", "қаз")
        days_list = self.days_kz if is_kz else self.days_ru
        schedule: list[dict[str, Any]] = []

        by_day: dict[int, list[dict[str, Any]]] = {i: [] for i in range(1, 7)}
        for r in records:
            d_idx = self.get_day_index(r)
            if 1 <= d_idx <= 6:
                by_day[d_idx].append(r)

        for day_num in range(1, 7):
            day_name = days_list[day_num - 1]
            day_records = by_day[day_num]
            day_records.sort(key=lambda r: r.get("slot") or 0)

            time_slot_map: dict[str, str] = {}

            for r in day_records:
                time_slot_label = r.get("time") or (f"Пара {r['slot']}" if r.get("slot") else "")
                time_val = self.normalize_time(time_slot_label)
                if not time_val:
                    continue

                # 1. Subject and lesson type
                raw_subj = (r.get("subject") or r.get("discipline") or "").strip()
                subject_line = ""
                if raw_subj:
                    type_badge = self.format_lesson_type(r.get("type") or r.get("lessonType"))
                    if type_badge and type_badge.lower() not in raw_subj.lower():
                        subject_line = f"{raw_subj} {type_badge}".strip()
                    else:
                        subject_line = raw_subj

                # 2. Teacher name and classroom
                teacher_full = ""
                if r.get("teacher"):
                    teacher_full = teacher_directory_service.format_teacher_for_student(
                        r["teacher"], language
                    )
                room_str = self.format_room(r.get("room"), r.get("building"))

                detail_line = ""
                if teacher_full and room_str:
                    if len(teacher_full) > 26 or (len(teacher_full) + len(room_str) + 1 > 35):
                        detail_line = f"{teacher_full}\n{room_str}"
                    else:
                        detail_line = f"{teacher_full} {room_str}"
                elif teacher_full:
                    detail_line = teacher_full
                elif room_str:
                    detail_line = room_str

                item_lines = [line for line in [subject_line, detail_line] if line]
                if not item_lines:
                    continue
                lesson_block = "\n".join(item_lines)

                if time_val in time_slot_map:
                    existing_blocks = time_slot_map[time_val].split("\n📚 ")
                    if lesson_block not in existing_blocks:
                        time_slot_map[time_val] += f"\n📚 {lesson_block}"
                else:
                    time_slot_map[time_val] = lesson_block

            subjects = [{"time": t, "subject": subj} for t, subj in time_slot_map.items()]
            schedule.append({"day": day_name, "subjects": subjects})

        return schedule

    def adapt_teacher_schedule(
        self,
        records: list[dict[str, Any]] | None = None,
        language: str = "рус",
    ) -> list[dict[str, Any]]:
        """Adapts raw Buketov API records into teacher schedule."""
        records = records or []
        norm_lang = str(language or "").lower().strip()
        is_kz = norm_lang in ("каз", "kz", "kaz", "қаз")
        days_list = self.days_kz if is_kz else self.days_ru
        schedule: list[dict[str, Any]] = []

        by_day: dict[int, list[dict[str, Any]]] = {i: [] for i in range(1, 7)}
        for r in records:
            d_idx = self.get_day_index(r)
            if 1 <= d_idx <= 6:
                by_day[d_idx].append(r)

        for day_num in range(1, 7):
            day_name = days_list[day_num - 1]
            day_records = by_day[day_num]
            day_records.sort(key=lambda r: r.get("slot") or 0)

            time_slot_map: dict[str, dict[str, Any]] = {}

            for r in day_records:
                time_slot_label = r.get("time") or (f"Пара {r['slot']}" if r.get("slot") else "")
                time_val = self.normalize_time(time_slot_label)
                if not time_val:
                    continue

                room = str(r.get("room") or "").strip()
                building = str(r.get("building") or "").strip()
                raw_group = str(r.get("group") or "Занятие").strip()

                if room and building:
                    room_str = f" ({room})" if room.endswith(f"/{building}") else f" ({room}/{building})"
                elif room or building:
                    room_str = f" ({room or building})"
                else:
                    room_str = ""

                clean_group = _PAREN_RE.sub("", raw_group).strip()
                group_name = clean_group if (room_str and clean_group) else raw_group

                if time_val in time_slot_map:
                    slot_data = time_slot_map[time_val]
                    # Retain room/building from whichever record provides them
                    if not slot_data.get("room") and room:
                        slot_data["room"] = room
                    if not slot_data.get("building") and building:
                        slot_data["building"] = building
                    eff_room = slot_data.get("room") or room
                    eff_bld = slot_data.get("building") or building
                    if eff_room and eff_bld:
                        eff_room_str = f" ({eff_room})" if eff_room.endswith(f"/{eff_bld}") else f" ({eff_room}/{eff_bld})"
                    elif eff_room or eff_bld:
                        eff_room_str = f" ({eff_room or eff_bld})"
                    else:
                        eff_room_str = ""

                    if "groupsList" not in slot_data:
                        existing_clean = _PAREN_RE.sub("", slot_data.get("group", "")).strip()
                        slot_data["groupsList"] = [existing_clean]

                    if group_name not in slot_data["groupsList"]:
                        slot_data["groupsList"].append(group_name)
                    slot_data["group"] = f"{', '.join(slot_data['groupsList'])}{eff_room_str}"

                    if not slot_data.get("subject") and (r.get("subject") or r.get("discipline")):
                        slot_data["subject"] = (r.get("subject") or r.get("discipline") or "").strip()

                    if not slot_data.get("lessonType") and (r.get("type") or r.get("lessonType")):
                        slot_data["lessonType"] = self.format_lesson_type_badge(
                            r.get("type") or r.get("lessonType")
                        )
                else:
                    time_slot_map[time_val] = {
                        "time": time_val,
                        "group": f"{group_name}{room_str}",
                        "groupsList": [group_name],
                        "subject": (r.get("subject") or r.get("discipline") or "").strip(),
                        "lessonType": self.format_lesson_type_badge(r.get("type") or r.get("lessonType")),
                        "room": room,
                        "building": building,
                    }

            groups = list(time_slot_map.values())
            schedule.append({"day": day_name, "groups": groups})

        return schedule


# Singleton instance
schedule_api_adapter = ScheduleApiAdapter()
