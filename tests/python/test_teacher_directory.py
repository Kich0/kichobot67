"""Unit tests for TeacherDirectoryService and Cyrillic/Kazakh normalization."""

import pytest
from src.services.teacher_directory import (
    clean_titles,
    format_title_case,
    is_kazakh_text,
    normalize_cyrillic,
    teacher_directory_service,
)


def test_normalize_cyrillic_kazakh_and_russian():
    """Verifies that Kazakh specific letters are normalized to standard phonetics."""
    assert normalize_cyrillic("Қарағанды") == "караганды"
    assert normalize_cyrillic("Әуезов") == "ауезов"
    assert normalize_cyrillic("Ғабит") == "габит"
    assert normalize_cyrillic("Үміт") == "умит"
    assert normalize_cyrillic("Ұлан") == "улан"
    assert normalize_cyrillic("Ілияс") == "илияс"
    assert normalize_cyrillic("Өмір") == "омир"
    assert normalize_cyrillic("Һәм") == "хам"
    assert normalize_cyrillic("Ёлка") == "елка"


def test_normalize_cyrillic_homoglyphs():
    """Verifies that Latin keyboard typos/homoglyphs are replaced with Cyrillic."""
    assert normalize_cyrillic("a c e o p x k") == "а с е о р х к"
    assert normalize_cyrillic("i y s h j") == "и у с х й"


def test_normalize_cyrillic_spaced_patronymics():
    """Verifies that spaced Kazakh patronymics are merged."""
    assert normalize_cyrillic("Сағынтай ұлы") == "сагынтайулы"
    assert normalize_cyrillic("Серік қызы") == "сериккызы"
    assert normalize_cyrillic("Ахмет тегі") == "ахметтеги"


def test_normalize_cyrillic_punctuation_and_none():
    """Verifies that punctuation is cleaned to single spaces and None is safe."""
    assert normalize_cyrillic("Попова,  Н.  В.") == "попова н в"
    assert normalize_cyrillic(None) == ""
    assert normalize_cyrillic("") == ""


def test_is_kazakh_text():
    """Verifies Kazakh alphabet detection."""
    assert is_kazakh_text("Қазақстан") is True
    assert is_kazakh_text("Әдебиет") is True
    assert is_kazakh_text("Ғылым") is True
    assert is_kazakh_text("Россия") is False
    assert is_kazakh_text("Hello") is False
    assert is_kazakh_text("") is False
    assert is_kazakh_text(None) is False


def test_clean_titles():
    """Verifies that academic ranks, degrees, and prefixes are stripped cleanly."""
    assert clean_titles("ст.преп. Попова Н. В.") == "Попова Н. В."
    assert clean_titles("ст. пр. Нигай Е. В.") == "Нигай Е. В."
    assert clean_titles("доц. Капашева Г. А.") == "Капашева Г. А."
    assert clean_titles("проф. Лазарева Е. А.") == "Лазарева Е. А."
    assert clean_titles("ассоц.проф. Жунусова М. Қ.") == "Жунусова М. Қ."
    assert clean_titles("оқытушы Балжан А.") == "Балжан А."
    assert clean_titles("аға оқытушы Сейтжанов Н.") == "Сейтжанов Н."
    assert clean_titles("PhD докторы Төлегенов Б.") == "Төлегенов Б."
    assert clean_titles("м.т.ғ.к. Оралбек М.") == "Оралбек М."

    # Surnames starting with title-like roots must NOT be corrupted
    assert clean_titles("Доценко И. П.") == "Доценко И. П."
    assert clean_titles("Прохоров А. С.") == "Прохоров А. С."
    assert clean_titles("") == ""
    assert clean_titles(None) == ""


def test_format_title_case():
    """Verifies title case formatting with Kazakh characters."""
    assert format_title_case("ПОПОВА НАДЕЖДА ВИКТОРОВНА") == "Попова Надежда Викторовна"
    assert format_title_case("жунусова меруерт қасымхановна") == "Жунусова Меруерт Қасымхановна"
    assert format_title_case("КАСЫЛКАСОВА КАМИЛА НУРАЛИЕВНА") == "Касылкасова Камила Нуралиевна"
    assert format_title_case("") == ""
    assert format_title_case(None) == ""


def test_teacher_directory_loaded():
    """Verifies that teacher directory contains loaded verified profiles."""
    assert len(teacher_directory_service.teachers) >= 800


def test_teacher_directory_find_match_and_enrich():
    """Verifies teacher lookup and enrichment."""
    # Lookup by title-prefixed initials
    match = teacher_directory_service.find_match("ст.преп. Попова Н. В.")
    assert match is not None
    assert match.get("lastName") == "Попова"
    assert match.get("firstName") == "Надежда"

    # Format teacher for student schedule
    formatted = teacher_directory_service.format_teacher_for_student("ст.преп. Попова Н. В.")
    assert formatted == "Попова Надежда Викторовна"

    # Enrich dictionary
    enriched = teacher_directory_service.enrich({"name": "Попова Н. В."})
    assert enriched is not None
    assert enriched.get("fullName") == "Попова Надежда Викторовна"
    assert enriched.get("jobTitle") is not None


def test_teacher_directory_search_by_full_name():
    """Verifies prefix and keyword search across verified profiles."""
    # Short query (2 letters)
    short_results = teacher_directory_service.search_by_full_name("по")
    assert len(short_results) > 0
    assert any(t.get("lastName", "").startswith("Поп") for t in short_results)

    # Multi-token query
    multi_results = teacher_directory_service.search_by_full_name("Попова Надежда")
    assert len(multi_results) > 0
    assert multi_results[0].get("fullName") == "Попова Надежда Викторовна"


def test_format_initials():
    """Verifies format_initials standardizes names to surname and initials."""
    from src.services.teacher_directory import format_initials
    assert format_initials("Попова Надежда Викторовна") == "Попова Н. В."
    assert format_initials("ст.преп. Попова Н. В.") == "Попова Н. В."
    assert format_initials("Төлегенов") == "Төлегенов"
    assert format_initials("") == ""


def test_is_same_teacher():
    """Verifies symmetric resolution between different representations of teacher names."""
    from src.services.teacher_directory import is_same_teacher
    # Initials vs full name
    assert is_same_teacher("Попова Н. В.", "Попова Надежда Викторовна") is True
    # Word order permutation
    assert is_same_teacher("Н. В. Попова", "Попова Н. В.") is True
    # Different titles
    assert is_same_teacher("ст.преп. Попова Н. В.", "Попова Надежда") is True
    # Different teachers
    assert is_same_teacher("Попова Н. В.", "Нигай Е. В.") is False
    assert is_same_teacher("Попова Н. В.", "Попов А. В.") is False


def test_clean_titles_kazakh_degrees():
    """Verifies cleaning of Kazakh academic degrees e.g. к.т.ғ.н., к.п.ғ.н."""
    assert clean_titles("к.т.ғ.н. Сейтжанов Н.") == "Сейтжанов Н."
    assert clean_titles("к.п.ғ.н. Касымова А.") == "Касымова А."
    assert clean_titles("к.ф.ғ.к. Байжанов Е.") == "Байжанов Е."
