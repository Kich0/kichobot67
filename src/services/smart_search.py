"""Smart Search Service for Buketov KarU.

Implements high-speed text search across student groups and teachers
using RapidFuzz for typo tolerance, phonetic Kazakh/Russian normalization,
exact disambiguation, and 'Did you mean?' fuzzy suggestions.
"""

from __future__ import annotations

from enum import Enum
import re
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz.distance import Levenshtein

from src.core.logging import get_logger
from src.services.teacher_directory import (
    clean_titles,
    is_kazakh_text,
    normalize_cyrillic,
    teacher_directory_service,
)

logger = get_logger("smart_search")


class SearchResultType(str, Enum):
    """Categorized outcome of a smart text search query."""

    EXACT_TEACHER = "exact_teacher"
    EXACT_GROUP = "exact_group"
    TEACHERS_LIST = "teachers_list"
    GROUPS_LIST = "groups_list"
    COMBINED = "combined"
    FUZZY_SUGGESTIONS = "fuzzy_suggestions"
    NOT_FOUND = "not_found"
    WELCOME_FALLBACK = "welcome_fallback"


class FuzzySuggestion(BaseModel):
    """Fuzzy suggestion candidate for misspelled queries."""

    model_config = ConfigDict(extra="ignore")

    type: str  # "group" or "teacher"
    item: dict[str, Any]
    similarity: float


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Safely retrieves field from dict or model instance."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class SmartSearchResult(BaseModel):
    """Complete result object returned by SmartSearchService.search."""

    model_config = ConfigDict(extra="ignore")

    query: str
    user_language: str = "ru"
    result_type: SearchResultType
    exact_teacher: dict[str, Any] | None = None
    exact_group: dict[str, Any] | None = None
    teachers: list[dict[str, Any]] = Field(default_factory=list)
    groups: list[dict[str, Any]] = Field(default_factory=list)
    fuzzy_suggestions: list[FuzzySuggestion] = Field(default_factory=list)
    prefer_groups_first: bool = False


def calculate_similarity(s1: str, s2: str) -> float:
    """Calculates Levenshtein similarity ratio between two strings in [0.0, 1.0]."""
    l1 = len(s1)
    l2 = len(s2)
    if l1 == 0 or l2 == 0:
        return 0.0
    dist = Levenshtein.distance(s1.lower(), s2.lower())
    return max(0.0, 1.0 - (dist / max(l1, l2)))


class SmartSearchService:
    """In-memory smart search engine with Levenshtein and RapidFuzz support."""

    def __init__(self) -> None:
        self._teachers_cache: list[dict[str, Any]] | None = None
        self._groups_cache: list[dict[str, Any]] | None = None
        self._last_teachers_cache_time: float = 0.0
        self._last_groups_cache_time: float = 0.0
        self._cache_ttl = 3600.0  # 1 hour

    def invalidate_caches(self) -> None:
        """Clears cached teachers and groups."""
        self._teachers_cache = None
        self._groups_cache = None
        self._last_teachers_cache_time = 0.0
        self._last_groups_cache_time = 0.0

    @staticmethod
    def _ensure_teacher_indexed(t: Any) -> tuple[str, str, str, str, str, str, str, list[str], list[str], list[str]]:
        """Returns precomputed normalized teacher fields, caching them on dict items."""
        if isinstance(t, dict) and "_norm_full" in t:
            return (
                t["_name_val"],
                t["_norm_name"],
                t["_norm_full"],
                t["_norm_last"],
                t["_norm_first"],
                t["_norm_patr"],
                t["_comp_full"],
                t["_full_tokens"],
                t["_norm_aliases"],
                t["_comp_aliases"],
            )

        name_val = str(_get_field(t, "name") or _get_field(t, "fullName") or _get_field(t, "initials") or "").strip()
        full_val = str(_get_field(t, "fullName") or name_val).strip()
        last_val = str(_get_field(t, "lastName") or (name_val.split()[0] if name_val.split() else "")).strip()
        first_val = str(_get_field(t, "firstName") or "").strip()
        patr_val = str(_get_field(t, "patronymic") or "").strip()

        norm_name = normalize_cyrillic(name_val)
        norm_full = normalize_cyrillic(full_val)
        norm_last = normalize_cyrillic(last_val)
        norm_first = normalize_cyrillic(first_val)
        norm_patr = normalize_cyrillic(patr_val)
        comp_full = norm_full.replace(" ", "")
        full_tokens = norm_full.split()

        aliases = _get_field(t, "aliases")
        norm_aliases = [normalize_cyrillic(a) for a in aliases if a] if isinstance(aliases, list) else []
        comp_aliases = [na.replace(" ", "") for na in norm_aliases]

        if isinstance(t, dict):
            t["_name_val"] = name_val
            t["_norm_name"] = norm_name
            t["_norm_full"] = norm_full
            t["_norm_last"] = norm_last
            t["_norm_first"] = norm_first
            t["_norm_patr"] = norm_patr
            t["_comp_full"] = comp_full
            t["_full_tokens"] = full_tokens
            t["_norm_aliases"] = norm_aliases
            t["_comp_aliases"] = comp_aliases

        return (
            name_val,
            norm_name,
            norm_full,
            norm_last,
            norm_first,
            norm_patr,
            comp_full,
            full_tokens,
            norm_aliases,
            comp_aliases,
        )

    @staticmethod
    def _ensure_group_indexed(g: Any) -> tuple[str, str, str, list[str]]:
        """Returns precomputed normalized group fields, caching them on dict items."""
        if isinstance(g, dict) and "_comp_name" in g:
            return (
                g["_g_name"],
                g["_norm_name"],
                g["_comp_name"],
                g["_norm_tokens"],
            )

        g_name = str(_get_field(g, "name") or "").strip()
        norm_name = normalize_cyrillic(g_name)
        comp_name = norm_name.replace(" ", "")
        norm_tokens = norm_name.split()

        if isinstance(g, dict):
            g["_g_name"] = g_name
            g["_norm_name"] = norm_name
            g["_comp_name"] = comp_name
            g["_norm_tokens"] = norm_tokens

        return g_name, norm_name, comp_name, norm_tokens

    async def warmup(self) -> None:
        """Pre-loads and pre-indexes teachers and groups caches in RAM."""
        await self.get_all_teachers()
        await self.get_all_groups()

    async def get_all_teachers(self) -> list[dict[str, Any]]:
        """Retrieves and enriches all teachers from MongoDB or directory fallback."""
        now = time.time()
        if (
            self._teachers_cache is not None
            and (now - self._last_teachers_cache_time < self._cache_ttl)
            and len(self._teachers_cache) > 0
        ):
            return self._teachers_cache

        unique_teachers: list[dict[str, Any]] = []
        db_loaded = False
        try:
            from src.database.models.teacher import Teacher

            docs = await Teacher.find().sort("name").to_list()
            db_loaded = True
            seen: set[int] = set()

            for doc in docs:
                t_id = doc.teacher_id
                if t_id in seen:
                    continue
                seen.add(t_id)
                t_dict = doc.model_dump(by_alias=True)
                enriched = teacher_directory_service.enrich(t_dict)
                if enriched:
                    self._ensure_teacher_indexed(enriched)
                    unique_teachers.append(enriched)
        except Exception as e:
            logger.debug(f"Could not load teachers from DB, checking directory: {e}")

        # Combine DB teachers with in-memory teacher directory service
        all_teachers = list(unique_teachers)
        seen_names = {
            t.get("_norm_full") or normalize_cyrillic(str(_get_field(t, "fullName") or _get_field(t, "name") or ""))
            for t in unique_teachers
            if (_get_field(t, "fullName") or _get_field(t, "name"))
        }
        for d_t in (teacher_directory_service.teachers or []):
            d_norm = normalize_cyrillic(str(_get_field(d_t, "fullName") or _get_field(d_t, "name") or ""))
            if d_norm and d_norm not in seen_names:
                seen_names.add(d_norm)
                enriched = teacher_directory_service.enrich(d_t)
                if enriched:
                    self._ensure_teacher_indexed(enriched)
                    all_teachers.append(enriched)

        if db_loaded:
            self._teachers_cache = all_teachers
            self._last_teachers_cache_time = now
        return all_teachers

    async def get_all_groups(self) -> list[dict[str, Any]]:
        """Retrieves all student groups from MongoDB."""
        now = time.time()
        if (
            self._groups_cache is not None
            and (now - self._last_groups_cache_time < self._cache_ttl)
            and len(self._groups_cache) > 0
        ):
            return self._groups_cache

        try:
            from src.database.models.group import Group

            docs = await Group.find().sort("-id").to_list()
            seen: set[int] = set()
            unique_groups: list[dict[str, Any]] = []

            for doc in docs:
                g_id = doc.group_id
                if g_id in seen:
                    continue
                seen.add(g_id)
                g_dict = doc.model_dump(by_alias=True)
                self._ensure_group_indexed(g_dict)
                unique_groups.append(g_dict)

            self._groups_cache = unique_groups
            self._last_groups_cache_time = now
            return self._groups_cache
        except Exception as e:
            logger.debug(f"Could not load groups from DB: {e}")
            return []

    def find_teachers(
        self,
        name: str,
        teachers: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Finds teachers matching query with relevance scoring and typo tolerance."""
        teachers_list = teachers or []
        norm_query = normalize_cyrillic(name)
        if not norm_query:
            return list(teachers_list)

        tokens = norm_query.split()
        compact_query = norm_query.replace(" ", "")

        if not tokens:
            return list(teachers_list)

        matches: list[tuple[int, dict[str, Any]]] = []

        for t in teachers_list:
            if not t:
                continue

            (
                name_val,
                norm_name,
                norm_full,
                norm_last,
                norm_first,
                norm_patr,
                comp_full,
                full_tokens,
                norm_aliases,
                comp_aliases,
            ) = self._ensure_teacher_indexed(t)
            if not name_val:
                continue

            # 1. Short queries (<= 2 chars): strictly prefix of surname or first name
            if len(tokens) == 1 and len(norm_query) <= 2:
                last_starts = norm_last.startswith(norm_query)
                first_starts = bool(norm_first and norm_first.startswith(norm_query))
                if last_starts or first_starts:
                    matches.append((100 if last_starts else 60, t))
                continue

            # 2. Exact match on full name
            if norm_full == norm_query or comp_full == compact_query or norm_name == norm_query:
                matches.append((200, t))
                continue

            # 3. Exact surname
            if norm_last == norm_query:
                matches.append((150, t))
                continue

            # 4. Exact first name
            if norm_first and norm_first == norm_query:
                matches.append((140, t))
                continue

            # 5. Aliases match
            if norm_aliases:
                alias_matched = any(
                    na == norm_query
                    or ca == compact_query
                    or na.startswith(norm_query)
                    or (len(tokens) > 1 and norm_query in na)
                    for na, ca in zip(norm_aliases, comp_aliases)
                )
                if alias_matched:
                    matches.append((135, t))
                    continue

            # 6. Multi-word query: all tokens present in full name in any order
            if len(tokens) > 1 and all(tok in full_tokens for tok in tokens):
                matches.append((130, t))
                continue

            # 7. Surname starts with query
            if norm_last.startswith(norm_query):
                matches.append((110, t))
                continue

            # 8. First name starts with query
            if norm_first and norm_first.startswith(norm_query):
                matches.append((100, t))
                continue

            # 9. Multi-word prefix match
            if len(tokens) > 1 and all(any(w.startswith(tok) for w in full_tokens) for tok in tokens):
                matches.append((95, t))
                continue

            # 10. Patronymic starts with query
            if norm_patr and norm_patr.startswith(norm_query):
                matches.append((90, t))
                continue

            # 11. Initials prefix
            if norm_name.startswith(norm_query) or norm_name.replace(" ", "").startswith(compact_query):
                matches.append((85, t))
                continue

            # 12. 1-letter typo in surname (len >= 4)
            if len(tokens) == 1 and len(norm_query) >= 4 and len(norm_last) >= 4:
                if Levenshtein.distance(norm_last, norm_query) == 1:
                    matches.append((84, t))
                    continue

            # 13. 1-letter typo in first name (len >= 4)
            if len(tokens) == 1 and len(norm_query) >= 4 and len(norm_first) >= 4:
                if Levenshtein.distance(norm_first, norm_query) == 1:
                    matches.append((82, t))
                    continue

            # 14. Multi-word query with 1 total typo
            if len(tokens) > 1:
                total_typos = 0
                all_match = True
                for q_tok in tokens:
                    if any(w.startswith(q_tok) for w in full_tokens):
                        continue
                    if len(q_tok) >= 4:
                        typo_match = False
                        for w in full_tokens:
                            for length in [len(q_tok) - 1, len(q_tok), len(q_tok) + 1]:
                                if 3 <= length <= len(w):
                                    if Levenshtein.distance(w[:length], q_tok) <= 1:
                                        typo_match = True
                                        break
                            if typo_match:
                                break
                        if typo_match:
                            total_typos += 1
                            continue
                    all_match = False
                    break

                if all_match and total_typos <= 1:
                    matches.append((78, t))
                    continue

            # 15. Compact match
            if len(compact_query) >= 3 and comp_full.startswith(compact_query):
                matches.append((72, t))
                continue

        matches.sort(key=lambda item: -item[0])
        return [item[1] for item in matches]

    def find_groups(
        self,
        name: str,
        groups: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Finds student groups matching query with abbreviation prefix and typo handling."""
        groups_list = groups or []
        norm_query = normalize_cyrillic(name)
        tokens = norm_query.split()
        compact_query = norm_query.replace(" ", "")

        if not tokens:
            return list(groups_list)

        results: list[dict[str, Any]] = []

        for g in groups_list:
            if not g:
                continue
            g_name, norm_name, comp_name, norm_tokens = self._ensure_group_indexed(g)
            if not g_name:
                continue

            # 1. Compact startswith ("ис" -> "ис211")
            if comp_name.startswith(compact_query):
                results.append(g)
                continue

            # 2. All tokens match prefix of words
            if tokens and all(any(n_tok == q_tok or n_tok.startswith(q_tok) for n_tok in norm_tokens) for q_tok in tokens):
                results.append(g)
                continue

            # 3. Compact substring
            if len(compact_query) >= 2 and compact_query in comp_name:
                results.append(g)
                continue

            # 4. Typo tolerance of 1 letter for queries >= 4 chars
            if len(compact_query) >= 4:
                matched_typo = False
                for length in [len(compact_query) - 1, len(compact_query), len(compact_query) + 1]:
                    if 3 <= length <= len(comp_name):
                        prefix = comp_name[:length]
                        if Levenshtein.distance(prefix, compact_query) <= 1:
                            matched_typo = True
                            break
                if matched_typo or Levenshtein.distance(comp_name, compact_query) <= 1:
                    results.append(g)
                    continue

        # Sort: exact compact first, prefix compact next, alphabetical
        def _sort_key(item: Any) -> tuple[int, int, str]:
            raw_n, _, n, _ = self._ensure_group_indexed(item)
            is_exact = 0 if n == compact_query else 1
            is_prefix = 0 if n.startswith(compact_query) else 1
            return (is_exact, is_prefix, raw_n)

        results.sort(key=_sort_key)
        return results

    def find_fuzzy_suggestions(
        self,
        raw_query: str,
        teachers: list[dict[str, Any]] | None = None,
        groups: list[dict[str, Any]] | None = None,
    ) -> list[FuzzySuggestion]:
        """Calculates fuzzy similarity for misspelled queries (returns top 3)."""
        norm_q = normalize_cyrillic(raw_query)
        compact = norm_q.replace(" ", "")
        if len(compact) < 3:
            return []

        compact_no_suffix = re.sub(r"(\d+)[ркkzru]+$", r"\1", compact, flags=re.IGNORECASE)
        suggestions: list[FuzzySuggestion] = []

        # 1. Groups
        for g in groups or []:
            if not g:
                continue
            g_name, _, g_comp, _ = self._ensure_group_indexed(g)
            if not g_name:
                continue
            sim1 = calculate_similarity(compact, g_comp)
            sim2 = calculate_similarity(compact_no_suffix, g_comp)
            best_sim = max(sim1, sim2)

            if best_sim >= 0.60:
                g_dict = g if isinstance(g, dict) else (g.model_dump(by_alias=True) if hasattr(g, "model_dump") else getattr(g, "__dict__", {}))
                suggestions.append(FuzzySuggestion(type="group", item=g_dict, similarity=best_sim))

        # 2. Teachers
        for t in teachers or []:
            if not t:
                continue
            (
                name_val,
                _,
                _,
                norm_last,
                norm_first,
                _,
                comp_full,
                _,
                _,
                _,
            ) = self._ensure_teacher_indexed(t)
            if not name_val:
                continue

            sim_surname = calculate_similarity(compact, norm_last)
            sim_first = calculate_similarity(compact, norm_first) if norm_first else 0.0
            sim_full = calculate_similarity(compact, comp_full)
            best_sim = max(sim_surname, sim_first, sim_full)

            if best_sim >= 0.60:
                t_dict = t if isinstance(t, dict) else (t.model_dump(by_alias=True) if hasattr(t, "model_dump") else getattr(t, "__dict__", {}))
                suggestions.append(FuzzySuggestion(type="teacher", item=t_dict, similarity=best_sim))

        suggestions.sort(key=lambda s: -s.similarity)
        return suggestions[:3]

    async def search(
        self,
        query: str,
        user_language: str = "ru",
        teachers: list[dict[str, Any]] | None = None,
        groups: list[dict[str, Any]] | None = None,
    ) -> SmartSearchResult:
        """Executes full smart search workflow classifying result into explicit scenarios."""
        text = str(query or "").strip()
        lang = "kz" if is_kazakh_text(text) else user_language

        # Validation: length < 2 or no letters/digits
        if len(text) < 2 or not re.search(r"[a-zA-Zа-яА-ЯёЁәіңғүұқөһӘІҢҒҮҰҚӨҺ0-9]", text):
            return SmartSearchResult(
                query=text,
                user_language=lang,
                result_type=SearchResultType.WELCOME_FALLBACK,
            )

        teachers_pool = teachers if teachers is not None else await self.get_all_teachers()
        groups_pool = groups if groups is not None else await self.get_all_groups()

        matched_teachers = self.find_teachers(text, teachers_pool)
        matched_groups = self.find_groups(text, groups_pool)

        norm_q = normalize_cyrillic(text)
        compact_query = norm_q.replace(" ", "")

        # Check exact teacher match
        exact_teacher: dict[str, Any] | None = None
        if len(matched_teachers) == 1 and len(matched_groups) == 0:
            exact_teacher = matched_teachers[0]
        elif len(matched_teachers) > 0:
            query_tokens = norm_q.split()
            full_exact = []
            for t in matched_teachers:
                (
                    _,
                    n_name,
                    n_full,
                    _,
                    _,
                    _,
                    c_full,
                    full_tokens,
                    norm_aliases,
                    comp_aliases,
                ) = self._ensure_teacher_indexed(t)
                alias_matches = bool(norm_aliases) and any(
                    na == norm_q or ca == compact_query
                    for na, ca in zip(norm_aliases, comp_aliases)
                )
                if n_full == norm_q or n_name == norm_q or c_full == compact_query or alias_matches:
                    full_exact.append(t)
                    continue
                if len(query_tokens) > 1:
                    if all(tok in full_tokens for tok in query_tokens):
                        full_exact.append(t)
            if len(full_exact) == 1:
                exact_teacher = full_exact[0]

        # Check exact group match
        exact_group: dict[str, Any] | None = None
        if len(matched_groups) == 1 and len(matched_teachers) == 0:
            exact_group = matched_groups[0]
        elif len(matched_groups) > 0:
            group_exact = [
                g for g in matched_groups
                if self._ensure_group_indexed(g)[2] == compact_query
                or self._ensure_group_indexed(g)[1] == norm_q
            ]
            if len(group_exact) == 1:
                exact_group = group_exact[0]

        # Scenario 1: Exact teacher
        if exact_teacher and not exact_group:
            return SmartSearchResult(
                query=text,
                user_language=lang,
                result_type=SearchResultType.EXACT_TEACHER,
                exact_teacher=exact_teacher,
                teachers=matched_teachers,
                groups=matched_groups,
            )

        # Scenario 2: Exact group
        if exact_group and not exact_teacher:
            return SmartSearchResult(
                query=text,
                user_language=lang,
                result_type=SearchResultType.EXACT_GROUP,
                exact_group=exact_group,
                teachers=matched_teachers,
                groups=matched_groups,
            )

        # Scenario 3: Multiple teachers, 0 groups
        if len(matched_teachers) > 0 and len(matched_groups) == 0:
            return SmartSearchResult(
                query=text,
                user_language=lang,
                result_type=SearchResultType.TEACHERS_LIST,
                teachers=matched_teachers,
            )

        # Scenario 4: Multiple groups, 0 teachers
        if len(matched_groups) > 0 and len(matched_teachers) == 0:
            return SmartSearchResult(
                query=text,
                user_language=lang,
                result_type=SearchResultType.GROUPS_LIST,
                groups=matched_groups,
            )

        # Scenario 5: Combined (both teachers and groups)
        if len(matched_teachers) > 0 and len(matched_groups) > 0:
            has_digits = bool(re.search(r"\d", text))
            is_group_pattern = bool(re.match(r"^[a-zA-Zа-яА-ЯёЁ]{2,4}$", compact_query))
            group_starts = any(
                normalize_cyrillic(str(_get_field(g, "name", ""))).replace(" ", "").startswith(compact_query)
                for g in matched_groups
            )
            prefer_groups_first = has_digits or (is_group_pattern and group_starts)

            return SmartSearchResult(
                query=text,
                user_language=lang,
                result_type=SearchResultType.COMBINED,
                teachers=matched_teachers,
                groups=matched_groups,
                prefer_groups_first=prefer_groups_first,
            )

        # Scenario 6: Fuzzy suggestions
        fuzzy_suggestions = self.find_fuzzy_suggestions(text, teachers_pool, groups_pool)
        if fuzzy_suggestions:
            return SmartSearchResult(
                query=text,
                user_language=lang,
                result_type=SearchResultType.FUZZY_SUGGESTIONS,
                fuzzy_suggestions=fuzzy_suggestions,
            )

        # Scenario 7: Not found
        return SmartSearchResult(
            query=text,
            user_language=lang,
            result_type=SearchResultType.NOT_FOUND,
        )


# Singleton instance
smart_search_service = SmartSearchService()
