"""Teacher Directory Service for Buketov KarU.

Normalizes Cyrillic and Kazakh letters, cleans academic titles and degrees,
resolves homoglyphs, performs phonetic & initials matching, and loads
the verified 830+ teacher profiles from data/teachers_full_directory.json.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from src.core.logging import get_logger

logger = get_logger("teacher_directory")

# Regex for academic titles and ranks (supports Russian and Kazakh degrees e.g. к.т.ғ.н., PhD докторы)
TITLE_PATTERN = re.compile(
    r"^(?:ст\.?\s*преп(?:одаватель)?\.?|преп(?:одаватель)?\.?|ст\.?\s*пр\.?|пр\.?|доц(?:ент)?\.?|"
    r"проф(?:ессор)?\.?|асс(?:истент)?\.?|ассоц\.?\s*проф(?:ессор)?\.?|ассис\.?\s*проф(?:ессор)?\.?|"
    r"аcсоц\.?\s*проф(?:ессор)?\.?|аcсис\.?\s*проф(?:ессор)?\.?|аға\s+оқытушы|оқытушы|м\.т\.ғ\.к\.?|"
    r"п\.ғ\.к\.?|(?:к|д)\.[а-яё\-әіңғүұқөһ.]+\.?|phd(?:\s*докторы)?)\s+",
    re.IGNORECASE,
)

# Kazakh specific characters mapping
KZ_CYRILLIC_MAP = {
    "ә": "а",
    "і": "и",
    "ң": "н",
    "ғ": "г",
    "ү": "у",
    "ұ": "у",
    "қ": "к",
    "ө": "о",
    "һ": "х",
    "ё": "е",
}

# Latin homoglyphs mapping
LATIN_HOMOGLYPH_MAP = {
    "a": "а",
    "c": "с",
    "e": "е",
    "h": "х",
    "i": "и",
    "j": "й",
    "k": "к",
    "o": "о",
    "p": "р",
    "s": "с",
    "x": "х",
    "y": "у",
}

KAZAKH_LETTERS_PATTERN = re.compile(r"[әіңғүұқөһӘІҢҒҮҰҚӨҺ]")
PUNCTUATION_PATTERN = re.compile(r"[-_.,/\\()[\]{}|:;!?\"'`]")
SPACED_PATRONYMIC_PATTERN = re.compile(r"\s+(улы|кызы|теги)(?=\s|$)", re.IGNORECASE)
TITLE_CASE_WORD_PATTERN = re.compile(r"(?:^|[\s\-(/])[a-zA-Zа-яА-ЯёЁәіңғүұқөһӘІҢҒҮҰҚӨҺ]")


def normalize_cyrillic(text: str | None) -> str:
    """Normalizes Kazakh/Russian text and Latin homoglyphs for uniform search.

    Replaces specific Kazakh letters with general phonetics (Қ->к, Ә->а, etc.),
    replaces Latin homoglyphs (a, c, e, o, etc.), merges spaced Kazakh patronymics
    ("Сағынтай ұлы" -> "сагынтайулы"), and normalizes punctuation and whitespace.
    """
    if not text:
        return ""
    s = str(text).lower()

    for k, v in KZ_CYRILLIC_MAP.items():
        s = s.replace(k, v)

    for k, v in LATIN_HOMOGLYPH_MAP.items():
        s = s.replace(k, v)

    s = SPACED_PATRONYMIC_PATTERN.sub(r"\1", s)
    s = PUNCTUATION_PATTERN.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_kazakh_text(text: str | None) -> bool:
    """Checks whether text contains Kazakh-specific alphabet characters."""
    if not text:
        return False
    return bool(KAZAKH_LETTERS_PATTERN.search(str(text)))


def clean_titles(text: str | None) -> str:
    """Strips academic ranks, degrees, and prefixes without damaging surnames."""
    if not text:
        return ""
    s = str(text).strip()
    prev = ""
    while s != prev:
        prev = s
        s = TITLE_PATTERN.sub("", s).strip()
    return s


def format_title_case(text: str | None) -> str:
    """Standardizes full name to neat Title Case preserving Kazakh letters."""
    if not text:
        return ""
    s = str(text).lower()
    return TITLE_CASE_WORD_PATTERN.sub(lambda m: m.group(0).upper(), s)


def clean_latin_i(text: str | None) -> str:
    """Replaces accidental Latin 'i'/'I' in university directory with Kazakh 'і'/'І'."""
    if not text:
        return ""
    return str(text).replace("i", "і").replace("I", "І")


class TeacherDirectoryService:
    """In-memory directory and resolution engine for university teachers."""

    def __init__(
        self,
        data_path: Path | str | None = None,
        custom_path: Path | str | None = None,
    ) -> None:
        self.teachers: list[dict[str, Any]] = []
        self.by_id_map: dict[str, dict[str, Any]] = {}
        self.by_initials_map: dict[str, list[dict[str, Any]]] = {}
        self.by_single_initial_map: dict[str, list[dict[str, Any]]] = {}
        self.by_surname_map: dict[str, list[dict[str, Any]]] = {}
        self.by_full_name_map: dict[str, dict[str, Any]] = {}
        self._load_directory(data_path, custom_path)

    def _load_directory(
        self,
        data_path: Path | str | None = None,
        custom_path: Path | str | None = None,
    ) -> None:
        """Loads verified profiles from data/teachers_full_directory.json and custom."""
        try:
            root_dir = Path(__file__).resolve().parent.parent.parent
            main_file = Path(data_path) if data_path else root_dir / "data" / "teachers_full_directory.json"
            cust_file = (
                Path(custom_path)
                if custom_path is not None
                else root_dir / "data" / "teachers_custom_directory.json"
            )

            combined: list[dict[str, Any]] = []

            if main_file and str(main_file) and main_file.exists():
                with open(main_file, "r", encoding="utf-8") as f:
                    main_data = json.load(f)
                    if isinstance(main_data, list):
                        combined.extend(main_data)
            else:
                logger.warning(f"teachers_full_directory.json not found at {main_file}")

            if cust_file and str(cust_file) and cust_file.exists():
                with open(cust_file, "r", encoding="utf-8") as f:
                    cust_data = json.load(f)
                    if isinstance(cust_data, list):
                        combined.extend(cust_data)

            if combined:
                self.teachers = []
                for t in combined:
                    if not isinstance(t, dict):
                        continue
                    item = dict(t)
                    item["fullName"] = format_title_case(clean_latin_i(item.get("fullName", "")))
                    item["firstName"] = format_title_case(clean_latin_i(item.get("firstName", "")))
                    item["lastName"] = format_title_case(clean_latin_i(item.get("lastName", "")))
                    item["patronymic"] = format_title_case(clean_latin_i(item.get("patronymic", "")))
                    # Ensure name is always populated (using initials or full name)
                    if not item.get("name"):
                        item["name"] = item.get("initials") or item["fullName"]
                    self.teachers.append(item)

                self._build_indexes()
                logger.info(f"Loaded {len(self.teachers)} teacher profiles into directory")
        except Exception as e:
            logger.error(f"Error loading teacher directory: {e}")

    def _build_indexes(self) -> None:
        """Constructs fast in-memory lookup indexes."""
        self.by_id_map.clear()
        self.by_initials_map.clear()
        self.by_single_initial_map.clear()
        self.by_surname_map.clear()
        self.by_full_name_map.clear()

        for t in self.teachers:
            if not t:
                continue

            # 0. ID mapping
            t_id = t.get("id") or t.get("teacherId")
            if t_id is not None:
                self.by_id_map[str(t_id).strip()] = t

            # 1. Full name
            norm_full = normalize_cyrillic(t.get("fullName"))
            if norm_full:
                self.by_full_name_map[norm_full] = t

            # 2. Surname and initials
            norm_last = normalize_cyrillic(t.get("lastName"))
            if norm_last:
                self.by_surname_map.setdefault(norm_last, []).append(t)

                first_name_norm = normalize_cyrillic(t.get("firstName"))
                first_init = first_name_norm[0] if first_name_norm else ""
                if first_init:
                    single_key = f"{norm_last} {first_init}"
                    self.by_single_initial_map.setdefault(single_key, []).append(t)

                    patr_norm = normalize_cyrillic(t.get("patronymic"))
                    patr_init = patr_norm[0] if patr_norm else ""
                    if patr_init:
                        double_key = f"{norm_last} {first_init} {patr_init}"
                        self.by_initials_map.setdefault(double_key, []).append(t)

            # 3. Directory initials field
            if t.get("initials"):
                norm_inits = normalize_cyrillic(clean_titles(t["initials"]))
                if norm_inits:
                    entries = self.by_initials_map.setdefault(norm_inits, [])
                    if t not in entries:
                        entries.append(t)

            # 4. Aliases
            aliases = t.get("aliases")
            if isinstance(aliases, list):
                for alias in aliases:
                    clean_a = clean_titles(alias)
                    norm_a = normalize_cyrillic(clean_a)
                    if not norm_a:
                        continue

                    if norm_a not in self.by_full_name_map:
                        self.by_full_name_map[norm_a] = t

                    a_words = norm_a.split()
                    if len(a_words) == 1:
                        a_surname = a_words[0]
                        surname_list = self.by_surname_map.setdefault(a_surname, [])
                        if t not in surname_list:
                            surname_list.append(t)

                        f_norm = normalize_cyrillic(t.get("firstName"))
                        f_init = f_norm[0] if f_norm else ""
                        if f_init:
                            s_key = f"{a_surname} {f_init}"
                            single_list = self.by_single_initial_map.setdefault(s_key, [])
                            if t not in single_list:
                                single_list.append(t)

                            p_norm = normalize_cyrillic(t.get("patronymic"))
                            p_init = p_norm[0] if p_norm else ""
                            if p_init:
                                d_key = f"{a_surname} {f_init} {p_init}"
                                double_list = self.by_initials_map.setdefault(d_key, [])
                                if t not in double_list:
                                    double_list.append(t)
                    elif len(a_words) == 2 and len(a_words[1]) == 1:
                        single_list = self.by_single_initial_map.setdefault(norm_a, [])
                        if t not in single_list:
                            single_list.append(t)
                    elif len(a_words) >= 2:
                        init_list = self.by_initials_map.setdefault(norm_a, [])
                        if t not in init_list:
                            init_list.append(t)

    def find_match(
        self,
        raw_str: str | None,
        dept_id: int | str | None = None,
    ) -> dict[str, Any] | None:
        """Finds teacher in verified directory by any name, initials, or query string."""
        if not raw_str:
            return None
        clean = clean_titles(raw_str)
        norm = normalize_cyrillic(clean)
        if not norm:
            return None

        # 1. Exact full name
        if norm in self.by_full_name_map:
            return self.by_full_name_map[norm]

        words = norm.split()
        if not words:
            return None

        if len(words) >= 2 and len(words[0]) == 1:
            # Format: "И. О. Фамилия"
            surname = words[-1]
            inits = [w[0] for w in words[:-1]]
        else:
            # Format: "Фамилия И. О."
            surname = words[0]
            inits = [w[0] for w in words[1:]]

        # 2. Two initials: surname + inits[0] + inits[1]
        if len(inits) >= 2:
            key2 = f"{surname} {inits[0]} {inits[1]}"
            found2 = self.by_initials_map.get(key2)
            if found2:
                if len(found2) == 1:
                    return found2[0]
                if dept_id is not None:
                    match_dept = next(
                        (t for t in found2 if str(t.get("departmentId")) == str(dept_id)),
                        None,
                    )
                    if match_dept:
                        return match_dept
                return found2[0]

        # 3. Single initial: surname + inits[0]
        if len(inits) >= 1:
            key1 = f"{surname} {inits[0]}"
            found1 = self.by_single_initial_map.get(key1)
            if found1:
                if len(found1) == 1:
                    return found1[0]
                if dept_id is not None:
                    match_dept = next(
                        (t for t in found1 if str(t.get("departmentId")) == str(dept_id)),
                        None,
                    )
                    if match_dept:
                        return match_dept
                return found1[0]

        # 4. Surname only
        found_surname = self.by_surname_map.get(surname)
        if found_surname:
            if len(found_surname) == 1:
                return found_surname[0]
            if dept_id is not None:
                match_dept = next(
                    (t for t in found_surname if str(t.get("departmentId")) == str(dept_id)),
                    None,
                )
                if match_dept:
                    return match_dept
            return found_surname[0]

        return None

    def find_by_id(self, teacher_id: int | str | None) -> dict[str, Any] | None:
        """Finds teacher profile in directory by ID."""
        if teacher_id is None:
            return None
        return self.by_id_map.get(str(teacher_id).strip())

    def enrich(self, teacher: dict[str, Any] | str | None) -> dict[str, Any] | None:
        """Enriches raw teacher document or string with verified directory profile data."""
        if not teacher:
            return None

        if isinstance(teacher, str):
            raw_name = teacher
            teacher_dict: dict[str, Any] = {"name": raw_name}
            dept_id = None
        elif isinstance(teacher, dict):
            teacher_dict = dict(teacher)
            raw_name = teacher_dict.get("name") or teacher_dict.get("fullName") or ""
            dept_id = teacher_dict.get("department") or teacher_dict.get("departmentId")
        else:
            if hasattr(teacher, "model_dump"):
                teacher_dict = teacher.model_dump(by_alias=True)
                raw_name = teacher_dict.get("name") or teacher_dict.get("fullName") or ""
                dept_id = teacher_dict.get("department") or teacher_dict.get("departmentId")
                if hasattr(teacher, "teacher_id"):
                    teacher_dict["id"] = teacher.teacher_id
                    teacher_dict["teacher_id"] = teacher.teacher_id
                elif "id" in teacher_dict and (isinstance(teacher_dict["id"], int) or (isinstance(teacher_dict["id"], str) and teacher_dict["id"].isdigit())):
                    teacher_dict["teacher_id"] = int(teacher_dict["id"])
            else:
                raw_name = getattr(teacher, "name", None) or getattr(teacher, "full_name", "")
                teacher_dict = {"name": raw_name}
                if hasattr(teacher, "teacher_id"):
                    teacher_dict["id"] = teacher.teacher_id
                    teacher_dict["teacher_id"] = teacher.teacher_id
                elif hasattr(teacher, "id"):
                    val = getattr(teacher, "id")
                    if isinstance(val, int) or (isinstance(val, str) and val.isdigit()):
                        teacher_dict["id"] = int(val)
                        teacher_dict["teacher_id"] = int(val)
                dept_id = getattr(teacher, "department", None)

        if not raw_name:
            return teacher_dict

        matched = self.find_match(raw_name, dept_id)
        if matched:
            clean_full_name = format_title_case(matched.get("fullName", ""))
            teacher_dict["fullName"] = clean_full_name
            teacher_dict["firstName"] = format_title_case(matched.get("firstName", ""))
            teacher_dict["lastName"] = format_title_case(matched.get("lastName", ""))
            teacher_dict["patronymic"] = format_title_case(matched.get("patronymic", ""))
            teacher_dict["aliases"] = matched.get("aliases", [])
            teacher_dict["jobTitle"] = matched.get("jobTitle")
            teacher_dict["photoUrl"] = matched.get("photoUrl")
            teacher_dict["isHead"] = bool(matched.get("isHead", False))
            teacher_dict["departmentName"] = matched.get("departmentName")
            teacher_dict["departmentIdOnSite"] = matched.get("departmentId")
            if not teacher_dict.get("name"):
                teacher_dict["name"] = matched.get("name") or matched.get("initials") or clean_full_name
            return teacher_dict

        clean_raw = clean_titles(raw_name)
        if "fullName" not in teacher_dict:
            teacher_dict["fullName"] = clean_raw
        if not teacher_dict.get("name"):
            teacher_dict["name"] = clean_raw
        return teacher_dict

    def format_initials(self, full_name: str | None) -> str:
        """Formats full name or teacher string to standard initials: 'Фамилия И. О.'"""
        if not full_name:
            return ""
        clean = clean_titles(full_name)
        words = clean.split()
        if len(words) == 1:
            return format_title_case(words[0])
        match = self.find_match(clean)
        if match and match.get("initials"):
            return match["initials"]
        if len(words) >= 2:
            surname = format_title_case(words[0])
            inits = [f"{w[0].upper()}." for w in words[1:] if w]
            return f"{surname} {' '.join(inits)}".strip()
        return clean

    def is_same_teacher(self, name1: str | None, name2: str | None) -> bool:
        """Determines if two name strings refer to the same teacher."""
        if not name1 or not name2:
            return False
        clean1 = clean_titles(name1)
        clean2 = clean_titles(name2)
        match1 = self.find_match(clean1)
        match2 = self.find_match(clean2)
        if match1 and match2:
            # If both found in directory, compare unique full names
            return normalize_cyrillic(match1.get("fullName")) == normalize_cyrillic(match2.get("fullName"))
        # Fallback to initials and surname comparison
        norm1 = normalize_cyrillic(clean1).split()
        norm2 = normalize_cyrillic(clean2).split()
        if not norm1 or not norm2:
            return False
        # Compare surnames
        surname1 = norm1[-1] if len(norm1) >= 2 and len(norm1[0]) == 1 else norm1[0]
        surname2 = norm2[-1] if len(norm2) >= 2 and len(norm2[0]) == 1 else norm2[0]
        if surname1 != surname2:
            return False
        # Compare first initials if both present
        init1 = norm1[0][0] if len(norm1) >= 2 and len(norm1[0]) == 1 else (norm1[1][0] if len(norm1) > 1 else "")
        init2 = norm2[0][0] if len(norm2) >= 2 and len(norm2[0]) == 1 else (norm2[1][0] if len(norm2) > 1 else "")
        if init1 and init2 and init1 != init2:
            return False
        return True

    def format_teacher_for_student(
        self,
        raw_teacher_name: str | None,
        language: str = "рус",
    ) -> str:
        """Formats teacher name for student schedule display (returns clean full name)."""
        if not raw_teacher_name:
            return ""
        match = self.find_match(raw_teacher_name)
        if match and match.get("fullName"):
            return format_title_case(match["fullName"])
        return clean_titles(raw_teacher_name)

    def search_by_full_name(self, query: str) -> list[dict[str, Any]]:
        """Prefix/keyword search across teachers with relevance scoring."""
        norm_q = normalize_cyrillic(query)
        if not norm_q or len(norm_q) < 2:
            return []

        tokens = norm_q.split()
        if not tokens:
            return []

        scored_results: list[tuple[int, bool, str, dict[str, Any]]] = []

        for t in self.teachers:
            if not t or not t.get("fullName"):
                continue

            norm_full = normalize_cyrillic(t.get("fullName"))
            norm_last = normalize_cyrillic(t.get("lastName"))
            norm_first = normalize_cyrillic(t.get("firstName"))
            norm_patr = normalize_cyrillic(t.get("patronymic"))
            words = [w for w in [norm_last, norm_first, norm_patr] if w]

            # Short queries (<= 2 characters): strictly prefix of surname or first name
            if len(tokens) == 1 and len(norm_q) <= 2:
                if norm_last.startswith(norm_q):
                    scored_results.append((100, bool(t.get("isHead")), t.get("fullName", ""), t))
                elif norm_first.startswith(norm_q):
                    scored_results.append((50, bool(t.get("isHead")), t.get("fullName", ""), t))
                continue

            # Queries >= 3 characters:
            if norm_last == norm_q:
                scored_results.append((120, bool(t.get("isHead")), t.get("fullName", ""), t))
                continue

            if norm_first == norm_q:
                scored_results.append((115, bool(t.get("isHead")), t.get("fullName", ""), t))
                continue

            aliases = t.get("aliases")
            if isinstance(aliases, list):
                alias_match = False
                for a in aliases:
                    norm_a = normalize_cyrillic(a)
                    if norm_a == norm_q or norm_a.startswith(norm_q) or (len(tokens) > 1 and norm_q in norm_a):
                        alias_match = True
                        break
                if alias_match:
                    scored_results.append((110, bool(t.get("isHead")), t.get("fullName", ""), t))
                    continue

            if norm_last.startswith(norm_q):
                scored_results.append((95, bool(t.get("isHead")), t.get("fullName", ""), t))
                continue

            if norm_first.startswith(norm_q):
                scored_results.append((85, bool(t.get("isHead")), t.get("fullName", ""), t))
                continue

            if norm_patr and norm_patr.startswith(norm_q):
                scored_results.append((75, bool(t.get("isHead")), t.get("fullName", ""), t))
                continue

            all_tokens_prefix = all(any(w.startswith(tok) for w in words) for tok in tokens)
            if all_tokens_prefix:
                scored_results.append((105, bool(t.get("isHead")), t.get("fullName", ""), t))
                continue

        # Sort: score DESC, is_head DESC, full_name ASC
        scored_results.sort(key=lambda item: (-item[0], not item[1], item[2]))
        return [item[3] for item in scored_results]


# Singleton instance
teacher_directory_service = TeacherDirectoryService()


def format_initials(full_name: str | None) -> str:
    """Formats full name or teacher string to standard initials: 'Фамилия И. О.'"""
    return teacher_directory_service.format_initials(full_name)


def is_same_teacher(name1: str | None, name2: str | None) -> bool:
    """Determines if two name strings refer to the same teacher."""
    return teacher_directory_service.is_same_teacher(name1, name2)

