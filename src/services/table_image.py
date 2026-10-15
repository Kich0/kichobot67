"""SVG and PNG Table Image Generation Service for Teacher Schedules.

Ports TeacherTableImageService from Node.js with pixel-perfect visual styling,
adaptive column widths, dynamic row heights, two-column layouts for large streams,
Inter font rendering via resvg-py, asyncio thread offloading, and per-teacher locking.
"""

from __future__ import annotations

import asyncio
import hashlib
from html import escape
import json
from pathlib import Path
import re
from typing import Any

from cachetools import TTLCache

from src.core.constants import (
    CACHE_CAPACITY_TEACHER_IMAGES,
    CACHE_TEACHER_IMAGE_TTL,
)
from src.core.logging import get_logger

logger = get_logger("table_image")

# Regular expression to strip academic rank prefixes
TITLE_PREFIX_REGEX = re.compile(
    r"^(?:ст\.?\s*преп(?:одаватель)?\.?|преп(?:одаватель)?\.?|ст\.?\s*пр\.?|пр\.?|доц(?:ент)?\.?|проф(?:ессор)?\.?|асс(?:истент)?\.?|ассоц\.?\s*проф(?:ессор)?\.?|ассис\.?\s*проф(?:ессор)?\.?)\s+",
    re.IGNORECASE,
)

# Group and room extraction regex: matches group name and optional (room)
GROUP_ROOM_REGEX = re.compile(r"([^\s(),]+)\s*(?:\(([^)]+)\))?")

# Standard university pair intervals
STANDARD_TIMES = [
    "8.30-9.20",
    "9.30-10.20",
    "10.40-11.30",
    "11.40-12.30",
    "12.40-13.30",
    "13.40-14.30",
    "14.40-15.30",
    "15.50-16.40",
    "16.50-17.40",
    "17.50-18.40",
]

DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
DAYS_KZ = ["Дүйсенбі", "Сейсенбі", "Сәрсенбі", "Бейсенбі", "Жұма", "Сенбі"]

COLORS = {
    "canvas_bg": "#ffffff",
    "border": "#cbd5e1",
    "title_text": "#0f172a",
    "header_bg": "#475569",
    "header_text": "#ffffff",
    "header_sub": "#f1f5f9",
    "day_bg": "#e2e8f0",
    "day_text": "#1e293b",
    "busy_bg": "#15803d",  # Noble dark green
    "group_text": "#ffffff",
    "room_text": "#fef08a",  # Bright golden yellow for auditorium
    "subject_text": "#e2e8f0",  # Contrasting light grey for discipline
    "lesson_type_text": "#86efac",  # Soft mint green for badge
    "free_bg": "#b91c1c",  # Deep muted red for empty windows
    "dash_color": "#f8fafc",
}


def escape_xml(unsafe: Any) -> str:
    """Escapes XML/SVG special characters safely."""
    if unsafe is None:
        return ""
    s = str(unsafe)
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def normalize_time(t: str | None) -> str:
    """Normalizes time interval string (e.g. '08:30 - 09:20' -> '8.30-9.20')."""
    if not t:
        return ""
    res = re.sub(r"\s+", "", t)
    res = res.replace(":", ".")
    res = re.sub(r"[-–—]", "-", res)
    res = re.sub(r"(^|-)0(\d)", r"\1\2", res)
    return res


def parse_group_slot(raw_group: str | None) -> dict[str, Any]:
    """Parses raw group slot string into groups and unique rooms."""
    if not raw_group:
        return {"groups": [], "rooms": [], "group_str": "", "room_str": ""}

    groups: list[str] = []
    rooms: list[str] = []

    for m in GROUP_ROOM_REGEX.finditer(raw_group):
        g_val = m.group(1)
        r_val = m.group(2)
        if g_val:
            clean_g = g_val.replace(",", "").strip()
            if clean_g:
                groups.append(clean_g)
        if r_val:
            rooms.append(r_val.strip())

    unique_rooms: list[str] = []
    for r in rooms:
        if r not in unique_rooms:
            unique_rooms.append(r)

    return {
        "groups": groups,
        "rooms": unique_rooms,
        "group_str": ", ".join(groups),
        "room_str": ", ".join(unique_rooms),
    }


def split_subject_text(text: str | None, max_line: int = 22) -> list[str]:
    """Wraps subject text across at most two lines with ellipsis if necessary."""
    if not text:
        return []
    words = text.strip().split()
    if not words:
        return []
    if len(words) == 1:
        if len(words[0]) > max_line:
            return [words[0][: max_line - 1] + "…"]
        return [words[0]]

    lines: list[str] = []
    cur_line = ""

    for i, w in enumerate(words):
        if not cur_line:
            cur_line = w
        elif len(cur_line + " " + w) <= max_line:
            cur_line += " " + w
        else:
            lines.append(cur_line)
            cur_line = w
            if len(lines) >= 2:
                if i < len(words) - 1:
                    lines[1] = lines[1][: max_line - 2] + "…"
                break

    if cur_line and len(lines) < 2:
        lines.append(cur_line)

    return lines


def format_lesson_type_badge(lesson_type: str | None) -> str:
    """Formats lesson type badge label (e.g. Лекция, Прак.зан., Лаб.раб., СРОП)."""
    if not lesson_type:
        return ""
    lower = lesson_type.lower().strip()
    if "лекц" in lower or lower == "лек":
        return "(Лекция)"
    if "прак" in lower or "семин" in lower or lower == "пр":
        return "(Прак.зан.)"
    if "лаб" in lower:
        return "(Лаб.раб.)"
    if "сроп" in lower:
        return "(СРОП)"
    return f"({lesson_type.strip()})"


def wrap_groups_to_lines(groups: list[str], max_chars: int = 20) -> list[str]:
    """Combines a list of group names into lines with max length constraint."""
    if not groups:
        return []
    lines: list[str] = []
    cur_line = ""

    for g in groups:
        if not cur_line:
            cur_line = g
        elif len(cur_line + ", " + g) <= max_chars:
            cur_line += ", " + g
        else:
            lines.append(cur_line)
            cur_line = g
    if cur_line:
        lines.append(cur_line)
    return lines


def layout_cell_content(slot: dict[str, Any] | None, col_width: int = 200) -> dict[str, Any] | None:
    """Calculates layout lines, micro-columns, and dynamic row height for a table cell."""
    if not slot:
        return None
    raw_grp = slot.get("group")
    if not raw_grp or not str(raw_grp).strip():
        return None

    parsed = parse_group_slot(str(raw_grp))
    groups_list = slot.get("groupsList") or slot.get("groups_list") or parsed["groups"]

    room_str = parsed["room_str"]
    if not room_str and slot.get("room"):
        building = f"/{slot['building']}" if slot.get("building") else ""
        room_str = f"{slot['room']}{building}"

    max_subj_chars = max(18, int(col_width / 9.5))
    raw_subject = slot.get("subject")
    subject_lines = split_subject_text(str(raw_subject), max_subj_chars) if raw_subject else []

    raw_type = slot.get("lessonType") or slot.get("lesson_type")
    lesson_type = format_lesson_type_badge(str(raw_type)) if raw_type else ""

    is_large_stream = len(groups_list) >= 6
    group_lines: list[str] = []
    group_columns: dict[str, list[str]] | None = None

    if is_large_stream and col_width >= 200:
        mid = (len(groups_list) + 1) // 2
        col1 = groups_list[:mid]
        col2 = groups_list[mid:]
        group_columns = {"col1": col1, "col2": col2}
        line_count = max(len(col1), len(col2))
    else:
        max_group_chars = max(16, int(col_width / 9))
        group_lines = wrap_groups_to_lines(groups_list, max_group_chars)
        line_count = len(group_lines)

    if room_str:
        line_count += 1
    line_count += len(subject_lines)
    if lesson_type:
        line_count += 1

    estimated_height = max(100, 28 + line_count * 18)

    return {
        "groups_list": groups_list,
        "group_lines": group_lines,
        "group_columns": group_columns,
        "room_str": room_str,
        "subject_lines": subject_lines,
        "lesson_type": lesson_type,
        "line_count": line_count,
        "estimated_height": estimated_height,
    }


def _render_sync(svg_markup: str, fonts_dir: str | Path | None = None) -> bytes:
    """Synchronous CPU-bound SVG -> PNG render function using resvg-py."""
    import resvg_py

    font_dirs: list[str] = []
    if fonts_dir and Path(fonts_dir).is_dir():
        font_dirs.append(str(fonts_dir))
    else:
        project_root = Path(__file__).resolve().parent.parent.parent
        default_font_dir = project_root / "data" / "fonts"
        if default_font_dir.is_dir():
            font_dirs.append(str(default_font_dir))

    return resvg_py.svg_to_bytes(
        svg_string=svg_markup,
        font_dirs=font_dirs if font_dirs else None,
        width=1920,
        background="#ffffff",
        shape_rendering="geometric_precision",
        text_rendering="optimize_legibility",
    )


class TeacherTableImageService:
    """Generates visual weekly schedule tables for teachers in SVG and renders to PNG."""

    def __init__(self, fonts_dir: str | Path | None = None) -> None:
        self.fonts_dir = fonts_dir
        # Cache telegram file_id mapping: key -> file_id (TTL 20 min)
        self._file_id_cache: TTLCache[str, str] = TTLCache(
            maxsize=CACHE_CAPACITY_TEACHER_IMAGES,
            ttl=CACHE_TEACHER_IMAGE_TTL.total_seconds(),
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def get_teacher_lock(self, teacher_id: str | int) -> asyncio.Lock:
        """Retrieves or creates an asyncio.Lock specific to a teacher ID to prevent duplicate parallel renders."""
        key = str(teacher_id)
        async with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    @staticmethod
    def compute_schedule_hash(schedule_data: list[dict[str, Any]]) -> str:
        """Computes deterministic SHA-256 hash of schedule content."""
        try:
            raw = json.dumps(schedule_data, sort_keys=True, ensure_ascii=False)
        except Exception:
            raw = str(schedule_data)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def get_cached_file_id(self, teacher_id: str | int, lang: str, schedule_hash: str) -> str | None:
        """Returns cached Telegram file_id if present and not expired."""
        cache_key = f"{teacher_id}_{lang}_{schedule_hash}"
        return self._file_id_cache.get(cache_key)

    def cache_file_id(self, teacher_id: str | int, lang: str, schedule_hash: str, file_id: str) -> None:
        """Saves Telegram file_id into TTL cache."""
        cache_key = f"{teacher_id}_{lang}_{schedule_hash}"
        self._file_id_cache[cache_key] = file_id

    def invalidate_teacher(self, teacher_id: str | int) -> None:
        """Invalidates all cached file_ids for a teacher."""
        prefix = f"{teacher_id}_"
        keys_to_del = [k for k in self._file_id_cache if k.startswith(prefix)]
        for k in keys_to_del:
            self._file_id_cache.pop(k, None)

    invalidate_teacher_image = invalidate_teacher

    def build_svg(
        self,
        teacher: Any,
        schedule_data: list[dict[str, Any]] | None,
        lang: str = "ru",
    ) -> str:
        """Constructs responsive SVG markup with KarU visual theme."""
        norm_lang = "kz" if lang in ("kz", "kk") else "ru"
        default_days = [
            {"day": day, "groups": []}
            for day in (DAYS_KZ if norm_lang == "kz" else DAYS_RU)
        ]

        data = schedule_data if (schedule_data and isinstance(schedule_data, list)) else default_days

        # 1. Active slots trimming
        min_active_idx = -1
        max_active_idx = -1

        for idx, t in enumerate(STANDARD_TIMES):
            norm_t = normalize_time(t)
            has_slot = any(
                any(
                    normalize_time(g.get("time")) == norm_t
                    and g.get("group")
                    and str(g.get("group")).strip()
                    for g in d.get("groups", [])
                )
                for d in data
            )
            if has_slot:
                if min_active_idx == -1:
                    min_active_idx = idx
                max_active_idx = idx

        if min_active_idx == -1:
            active_times = STANDARD_TIMES[:6]
            start_pair_number = 1
        else:
            start = max(0, min_active_idx)
            end = min(len(STANDARD_TIMES) - 1, max_active_idx)
            # Ensure at least 5 pairs for a balanced grid
            if end - start + 1 < 5:
                if start > 0:
                    start = max(0, start - 1)
                if end < len(STANDARD_TIMES) - 1:
                    end = min(len(STANDARD_TIMES) - 1, end + 1)
            if end - start + 1 < 5 and end < len(STANDARD_TIMES) - 1:
                end = min(len(STANDARD_TIMES) - 1, start + 4)

            active_times = STANDARD_TIMES[start : end + 1]
            start_pair_number = start + 1

        times = active_times
        num_cols = len(times)

        # 2. Geometry calculations
        pad_x = 18
        pad_y = 18
        title_height = 56
        col_header_height = 48
        day_col_width = 115
        cell_gap = 5
        min_row_height = 100

        if num_cols <= 5:
            col_width = 265
        elif num_cols == 6:
            col_width = 240
        elif num_cols == 7:
            col_width = 215
        else:
            col_width = 195

        width = pad_x * 2 + day_col_width + num_cols * (col_width + cell_gap) - cell_gap

        # 3. Dynamic row heights
        row_layouts: list[list[dict[str, Any] | None]] = []
        row_heights: list[int] = []

        for day in data:
            cell_layouts: list[dict[str, Any] | None] = []
            max_row_h = min_row_height
            for t in times:
                norm_t = normalize_time(t)
                slot = None
                for g in day.get("groups", []):
                    if normalize_time(g.get("time")) == norm_t:
                        slot = g
                        break
                layout = layout_cell_content(slot, col_width)
                cell_layouts.append(layout)
                if layout and layout["estimated_height"] > max_row_h:
                    max_row_h = layout["estimated_height"]

            row_layouts.append(cell_layouts)
            row_heights.append(max_row_h)

        total_rows_height = sum(row_heights) + max(0, len(row_heights) - 1) * cell_gap
        height = pad_y * 2 + title_height + col_header_height + cell_gap + total_rows_height

        start_table_y = pad_y + title_height
        table_elements: list[str] = []

        # Header: "Day \ Time"
        day_time_label = "Күн \\ Уақыт" if norm_lang == "kz" else "День \\ Время"
        table_elements.append(
            f'<rect x="{pad_x}" y="{start_table_y}" width="{day_col_width}" height="{col_header_height}" rx="7" fill="{COLORS["header_bg"]}" />'
        )
        table_elements.append(
            f'<text x="{pad_x + day_col_width / 2:.1f}" y="{start_table_y + 29}" fill="{COLORS["header_text"]}" font-size="13.5" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{day_time_label}</text>'
        )

        # Header: Time columns
        pair_word = "сабақ" if norm_lang == "kz" else "пара"
        for i, t in enumerate(times):
            pair_num = start_pair_number + i
            x = pad_x + day_col_width + cell_gap + i * (col_width + cell_gap)
            table_elements.append(
                f'<rect x="{x}" y="{start_table_y}" width="{col_width}" height="{col_header_height}" rx="7" fill="{COLORS["header_bg"]}" />\n'
                f'<text x="{x + col_width / 2:.1f}" y="{start_table_y + 20}" fill="{COLORS["header_sub"]}" font-size="13" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{pair_num} {pair_word}</text>\n'
                f'<text x="{x + col_width / 2:.1f}" y="{start_table_y + 37}" fill="{COLORS["header_text"]}" font-size="12" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{t}</text>'
            )

        # Week day rows
        cur_y = start_table_y + col_header_height + cell_gap

        for r_idx, day in enumerate(data):
            row_h = row_heights[r_idx]
            cell_layouts = row_layouts[r_idx]
            day_name = escape_xml(day.get("day", ""))

            # Day label cell
            table_elements.append(
                f'<rect x="{pad_x}" y="{cur_y}" width="{day_col_width}" height="{row_h}" rx="7" fill="{COLORS["day_bg"]}" />\n'
                f'<text x="{pad_x + day_col_width / 2:.1f}" y="{cur_y + row_h / 2 + 5:.1f}" fill="{COLORS["day_text"]}" font-size="14.5" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{day_name}</text>'
            )

            # Slot cells
            for c_idx, t in enumerate(times):
                x = pad_x + day_col_width + cell_gap + c_idx * (col_width + cell_gap)
                layout = cell_layouts[c_idx]
                clip_id = f"clip-{r_idx}-{c_idx}"

                if layout:
                    text_elements: list[str] = []
                    if layout.get("group_columns"):
                        # Two micro-columns for large streams
                        cols = layout["group_columns"]
                        col1 = cols["col1"]
                        col2 = cols["col2"]
                        group_rows = max(len(col1), len(col2))
                        group_line_h = 15
                        group_block_h = group_rows * group_line_h
                        extra_lines = (1 if layout["room_str"] else 0) + len(layout["subject_lines"]) + (1 if layout["lesson_type"] else 0)
                        extra_line_h = 17
                        total_h = group_block_h + extra_lines * extra_line_h + 6
                        cell_cur_y = cur_y + (row_h - total_h) / 2 + 12
                        col1_x = x + col_width * 0.28
                        col2_x = x + col_width * 0.72

                        for g_idx in range(group_rows):
                            g1 = col1[g_idx] if g_idx < len(col1) else ""
                            g2 = col2[g_idx] if g_idx < len(col2) else ""
                            if g1:
                                text_elements.append(
                                    f'<text x="{col1_x:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["group_text"]}" font-size="11.5" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{escape_xml(g1)}</text>'
                                )
                            if g2:
                                text_elements.append(
                                    f'<text x="{col2_x:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["group_text"]}" font-size="11.5" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{escape_xml(g2)}</text>'
                                )
                            cell_cur_y += group_line_h

                        cell_cur_y += 4

                        if layout["room_str"]:
                            text_elements.append(
                                f'<text x="{x + col_width / 2:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["room_text"]}" font-size="12.5" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">Ауд. {escape_xml(layout["room_str"])}</text>'
                            )
                            cell_cur_y += extra_line_h

                        for s_line in layout["subject_lines"]:
                            text_elements.append(
                                f'<text x="{x + col_width / 2:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["subject_text"]}" font-size="12" font-weight="600" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{escape_xml(s_line)}</text>'
                            )
                            cell_cur_y += extra_line_h

                        if layout["lesson_type"]:
                            text_elements.append(
                                f'<text x="{x + col_width / 2:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["lesson_type_text"]}" font-size="11.5" font-weight="500" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{escape_xml(layout["lesson_type"])}</text>'
                            )
                    else:
                        # Standard single column layout
                        content_lines = len(layout["group_lines"]) + (1 if layout["room_str"] else 0) + len(layout["subject_lines"]) + (1 if layout["lesson_type"] else 0)
                        line_h = 18
                        total_h = (content_lines - 1) * line_h
                        cell_cur_y = cur_y + (row_h - total_h) / 2 + 4

                        for g_line in layout["group_lines"]:
                            f_size = 12 if len(layout["group_lines"]) > 2 else 13.5
                            text_elements.append(
                                f'<text x="{x + col_width / 2:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["group_text"]}" font-size="{f_size}" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{escape_xml(g_line)}</text>'
                            )
                            cell_cur_y += line_h

                        if layout["room_str"]:
                            text_elements.append(
                                f'<text x="{x + col_width / 2:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["room_text"]}" font-size="12.5" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">Ауд. {escape_xml(layout["room_str"])}</text>'
                            )
                            cell_cur_y += line_h

                        for s_line in layout["subject_lines"]:
                            text_elements.append(
                                f'<text x="{x + col_width / 2:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["subject_text"]}" font-size="12" font-weight="600" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{escape_xml(s_line)}</text>'
                            )
                            cell_cur_y += line_h

                        if layout["lesson_type"]:
                            text_elements.append(
                                f'<text x="{x + col_width / 2:.1f}" y="{cell_cur_y:.1f}" fill="{COLORS["lesson_type_text"]}" font-size="11.5" font-weight="500" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{escape_xml(layout["lesson_type"])}</text>'
                            )

                    table_elements.append(
                        f'<clipPath id="{clip_id}">\n'
                        f'  <rect x="{x}" y="{cur_y}" width="{col_width}" height="{row_h}" rx="7" />\n'
                        f'</clipPath>\n'
                        f'<rect x="{x}" y="{cur_y}" width="{col_width}" height="{row_h}" rx="7" fill="{COLORS["busy_bg"]}" />\n'
                        f'<g clip-path="url(#{clip_id})">\n'
                        f'  {"".join(text_elements)}\n'
                        f'</g>'
                    )
                else:
                    # Empty window
                    table_elements.append(
                        f'<rect x="{x}" y="{cur_y}" width="{col_width}" height="{row_h}" rx="7" fill="{COLORS["free_bg"]}" />\n'
                        f'<text x="{x + col_width / 2:.1f}" y="{cur_y + row_h / 2 + 7:.1f}" fill="{COLORS["dash_color"]}" font-size="24" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">-</text>'
                    )

            cur_y += row_h + cell_gap

        # Title text
        raw_name = teacher if isinstance(teacher, str) else (
            teacher.get("fullName") or teacher.get("name") if isinstance(teacher, dict)
            else getattr(teacher, "full_name", None) or getattr(teacher, "name", "Преподаватель")
        )
        clean_name = TITLE_PREFIX_REGEX.sub("", str(raw_name))
        title_prefix = "Оқытушының жүктемесі:" if norm_lang == "kz" else "Загруженность:"
        title = f"{title_prefix} {clean_name}"

        return (
            f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">\n'
            f'  <rect width="{width}" height="{height}" rx="10" fill="{COLORS["canvas_bg"]}" stroke="{COLORS["border"]}" stroke-width="1" />\n'
            f'  <text x="{width / 2:.1f}" y="{pad_y + 34}" fill="{COLORS["title_text"]}" font-size="22" font-weight="bold" text-anchor="middle" font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif">{escape_xml(title)}</text>\n'
            f'  {"".join(table_elements)}\n'
            f'</svg>'
        )

    async def render_table_image(
        self,
        teacher: Any,
        schedule_data: list[dict[str, Any]] | None,
        lang: str = "ru",
    ) -> bytes:
        """Asynchronously builds SVG and renders PNG bytes off-loop."""
        teacher_id = teacher if isinstance(teacher, (str, int)) else (
            teacher.get("id") or teacher.get("_id") or teacher.get("name") if isinstance(teacher, dict)
            else getattr(teacher, "teacher_id", None) or getattr(teacher, "name", "teacher")
        )

        lock = await self.get_teacher_lock(str(teacher_id))
        async with lock:
            svg_markup = self.build_svg(teacher, schedule_data, lang)
            return await asyncio.to_thread(_render_sync, svg_markup, self.fonts_dir)


# Global singleton instance
table_image_service = TeacherTableImageService()

__all__ = [
    "COLORS",
    "DAYS_KZ",
    "DAYS_RU",
    "STANDARD_TIMES",
    "TeacherTableImageService",
    "escape_xml",
    "format_lesson_type_badge",
    "layout_cell_content",
    "normalize_time",
    "parse_group_slot",
    "split_subject_text",
    "table_image_service",
    "wrap_groups_to_lines",
]
