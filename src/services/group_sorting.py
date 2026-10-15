"""Natural sorting engine for academic groups and teachers.

Implements:
1. Reverse year sorting for groups: newest admission year first (1st course e.g. 26 -> 25 -> 24 -> 23).
2. Natural sorting for subgroup/stream suffixes: '1' before '2', '1' before '1к', etc.
3. Fallback to group 'age' (course 1..4) if year is absent.
4. Alphabetical sorting for teachers by surname with support for Kazakh Cyrillic
   (Ә, Ғ, Қ, Ң, Ө, Ұ, Ү, Һ, І) and Russian Cyrillic characters.
"""

from __future__ import annotations

import re
from typing import Any


# Kazakh Cyrillic collation mapping
KZ_CYRILLIC_ORDER = "аәбвгғдеёжзийкқлмнңоөпрстуұүфхһцчшщъыіьэюя"
KZ_CHAR_MAP = {ch: idx for idx, ch in enumerate(KZ_CYRILLIC_ORDER)}


def kz_ru_sort_key(text: str) -> list[int]:
    """Generates a sort key supporting Kazakh and Russian Cyrillic collation."""
    if not text:
        return []
    clean = text.lower().strip()
    key: list[int] = []
    for ch in clean:
        if ch in KZ_CHAR_MAP:
            key.append(KZ_CHAR_MAP[ch])
        else:
            # Fallback to standard unicode code point offset above cyrillic
            key.append(1000 + ord(ch))
    return key


def natural_split_key(text: str) -> list[Any]:
    """Splits a string into digit and non-digit chunks for natural sorting."""
    chunks = re.split(r"(\d+)", text)
    result: list[Any] = []
    for c in chunks:
        if not c:
            continue
        if c.isdigit():
            result.append(int(c))
        else:
            result.append(kz_ru_sort_key(c))
    return result


def group_sort_key(group: Any) -> tuple[str, int, list[Any], str]:
    """Extracts a comparison key for student groups.

    Structure:
    - Prefix letters: e.g. 'ИС-', 'ВТПО-', 'ЮР-' (alphabetical).
    - Admission year: -int(year), so 26 (1st course) comes before 25, 24, 23 (descending).
    - Subgroup chunks: natural sort for suffixes like '-1', '-1к', '-2', '-2к'.
    - Original name fallback.
    """
    raw_name = ""
    age: int | None = None

    if hasattr(group, "name"):
        raw_name = str(group.name or "")
        age = getattr(group, "age", None)
    elif isinstance(group, dict):
        raw_name = str(group.get("name") or "")
        age = group.get("age")
    else:
        raw_name = str(group or "")

    name = raw_name.strip()
    if not name:
        return ("", 0, [], "")

    # Match: (prefix with optional hyphen)(2 or 4 digits year)(remaining suffix)
    # Examples:
    # "ИС-26-1" -> prefix="ИС", year="26", suffix="-1"
    # "ВТПО-25-1к" -> prefix="ВТПО", year="25", suffix="-1к"
    # "Ю-23" -> prefix="Ю", year="23", suffix=""
    # "ИС 26-2" -> prefix="ИС", year="26", suffix="-2"
    m = re.match(r"^([^\d]+?)[\s\-_]*(\d{2,4})(.*)$", name, flags=re.IGNORECASE)
    if m:
        prefix = m.group(1).strip().upper()
        year_str = m.group(2)
        suffix = m.group(3) or ""

        # Normalize 4-digit year like 2026 to 26
        if len(year_str) == 4 and (year_str.startswith("20") or year_str.startswith("19")):
            year_val = int(year_str[2:])
        else:
            year_val = int(year_str)

        # Descending: -year_val puts 26 before 25, 24, 23
        return (prefix, -year_val, natural_split_key(suffix), name)

    # If no year in name but age (course 1..4) is specified:
    # Course 1 (newest) -> year equivalent, course 4 (oldest)
    if age and isinstance(age, int) and age > 0:
        # e.g. age=1 -> -1 (comes first), age=4 -> -4
        return (name.upper(), age, natural_split_key(name), name)

    # Pure natural string sort fallback
    return (name.upper(), 0, natural_split_key(name), name)


def sort_groups(groups: list[Any]) -> list[Any]:
    """Sorts groups so 1st course (newest year) comes first, followed by older courses."""
    if not groups:
        return []
    return sorted(groups, key=group_sort_key)


def teacher_sort_key(teacher: Any) -> list[int]:
    """Generates a sorting key for teachers based on their full name or surname."""
    name = ""
    if hasattr(teacher, "name"):
        name = getattr(teacher, "name", "") or ""
    elif isinstance(teacher, dict):
        name = teacher.get("fullName") or teacher.get("name") or ""
    else:
        name = str(teacher or "")

    return kz_ru_sort_key(name)


def sort_teachers(teachers: list[Any]) -> list[Any]:
    """Sorts teachers alphabetically by surname with full Kazakh & Russian collation."""
    if not teachers:
        return []
    return sorted(teachers, key=teacher_sort_key)


__all__ = [
    "group_sort_key",
    "kz_ru_sort_key",
    "sort_groups",
    "sort_teachers",
    "teacher_sort_key",
]
