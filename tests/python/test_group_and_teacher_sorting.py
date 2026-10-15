"""Unit tests for group and teacher sorting algorithms."""

import pytest

from src.services.group_sorting import (
    group_sort_key,
    kz_ru_sort_key,
    sort_groups,
    sort_teachers,
    teacher_sort_key,
)


def test_group_sorting_descending_year_newest_first():
    """Groups must be sorted with 1st course (newest year, e.g. 26) first, down to older years."""
    raw_names = [
        "ИС-23-1к",
        "ИС-26-1",
        "ИС-25-2",
        "ИС-24-1",
        "ИС-26-2",
        "ИС-23-2",
        "ИС-25-1",
        "ИС-26-1к",
    ]
    sorted_groups = sort_groups(raw_names)
    sorted_names = [g if isinstance(g, str) else g.name for g in sorted_groups]

    expected = [
        "ИС-26-1",
        "ИС-26-1к",
        "ИС-26-2",
        "ИС-25-1",
        "ИС-25-2",
        "ИС-24-1",
        "ИС-23-1к",
        "ИС-23-2",
    ]
    assert sorted_names == expected


def test_group_sorting_with_dict_objects():
    """Sorting works seamlessly with dictionaries containing 'name'."""
    items = [
        {"id": 1, "name": "ФИЗ-23-1"},
        {"id": 2, "name": "ФИЗ-26-1"},
        {"id": 3, "name": "ФИЗ-25-1"},
    ]
    sorted_items = sort_groups(items)
    names = [item["name"] for item in sorted_items]
    assert names == ["ФИЗ-26-1", "ФИЗ-25-1", "ФИЗ-23-1"]


def test_teacher_sorting_kazakh_and_russian():
    """Teachers must be sorted alphabetically by surname, handling Kazakh Cyrillic characters."""
    teachers = [
        {"id": 1, "name": "Садыков А. Б."},
        {"id": 2, "name": "Әбдірахман Н. С."},
        {"id": 3, "name": "Ахметов Б. К."},
        {"id": 4, "name": "Ғабдуллин Е. М."},
        {"id": 5, "name": "Бакиров Д. Т."},
        {"id": 6, "name": "Қасымов М. О."},
        {"id": 7, "name": "Иванов И. И."},
        {"id": 8, "name": "Өмірзақ С. Т."},
    ]
    sorted_teachers = sort_teachers(teachers)
    names = [t["name"] for t in sorted_teachers]

    # In Kazakh alphabet:
    # А, Ә, Б, В, Г, Ғ, Д, Е, Ё, Ж, З, И, Й, К, Қ, Л, М, Н, Ң, О, Ө, П, Р, С, Т, У, Ұ, Ү, Ф, Х, Һ, Ц, Ч, Ш, Щ, Ъ, Ы, І, Ь, Э, Ю, Я
    assert names[0] == "Ахметов Б. К."
    assert names[1] == "Әбдірахман Н. С."
    assert names[2] == "Бакиров Д. Т."
    assert names[3] == "Ғабдуллин Е. М."
    assert names[4] == "Иванов И. И."
    assert names[5] == "Қасымов М. О."
    assert names[6] == "Өмірзақ С. Т."
    assert names[7] == "Садыков А. Б."
