import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from src.services.table_image import (
    COLORS,
    DAYS_KZ,
    DAYS_RU,
    STANDARD_TIMES,
    TeacherTableImageService,
    escape_xml,
    format_lesson_type_badge,
    layout_cell_content,
    normalize_time,
    parse_group_slot,
    split_subject_text,
    table_image_service,
    wrap_groups_to_lines,
)


def test_escape_xml():
    assert escape_xml(None) == ""
    assert escape_xml("") == ""
    assert escape_xml(123) == "123"
    assert escape_xml('<div class="test">& \'hello\'</div>') == (
        "&lt;div class=&quot;test&quot;&gt;&amp; &apos;hello&apos;&lt;/div&gt;"
    )


def test_normalize_time():
    assert normalize_time(None) == ""
    assert normalize_time("") == ""
    assert normalize_time("08:30 - 09:20") == "8.30-9.20"
    assert normalize_time("10:40–11:30") == "10.40-11.30"
    assert normalize_time(" 14 : 40 — 15 : 30 ") == "14.40-15.30"


def test_parse_group_slot():
    assert parse_group_slot(None) == {
        "groups": [],
        "rooms": [],
        "group_str": "",
        "room_str": "",
    }
    assert parse_group_slot("") == {
        "groups": [],
        "rooms": [],
        "group_str": "",
        "room_str": "",
    }

    parsed = parse_group_slot("ИС-21-1 (105/1), ПИ-21-1 (105/1)")
    assert parsed["groups"] == ["ИС-21-1", "ПИ-21-1"]
    assert parsed["rooms"] == ["105/1"]
    assert parsed["group_str"] == "ИС-21-1, ПИ-21-1"
    assert parsed["room_str"] == "105/1"

    parsed_multi = parse_group_slot("А-11 (201), Б-12 (302)")
    assert parsed_multi["groups"] == ["А-11", "Б-12"]
    assert parsed_multi["rooms"] == ["201", "302"]
    assert parsed_multi["room_str"] == "201, 302"


def test_split_subject_text():
    assert split_subject_text(None) == []
    assert split_subject_text("") == []
    assert split_subject_text("Математика") == ["Математика"]
    # Long single word truncation
    assert split_subject_text("ВысшаяМатематикаИТеорияВероятностейОченьДлинноеСлово", max_line=15) == [
        "ВысшаяМатемати…"
    ]

    # Two lines without overflow beyond loop
    lines = split_subject_text("Алгоритмы и структуры данных в программировании", max_line=20)
    assert lines == ["Алгоритмы и", "структуры данных в"]

    # Two lines with overflow beyond second line adds ellipsis
    lines_trunc = split_subject_text("Алгоритмы и структуры данных в программировании на Python", max_line=20)
    assert len(lines_trunc) == 2
    assert lines_trunc[1].endswith("…")


def test_format_lesson_type_badge():
    assert format_lesson_type_badge(None) == ""
    assert format_lesson_type_badge("") == ""
    assert format_lesson_type_badge("Лекция") == "(Лекция)"
    assert format_lesson_type_badge("лек") == "(Лекция)"
    assert format_lesson_type_badge("Практическое занятие") == "(Прак.зан.)"
    assert format_lesson_type_badge("семинар") == "(Прак.зан.)"
    assert format_lesson_type_badge("Лабораторная работа") == "(Лаб.раб.)"
    assert format_lesson_type_badge("СРОП") == "(СРОП)"
    assert format_lesson_type_badge("Консультация") == "(Консультация)"


def test_wrap_groups_to_lines():
    assert wrap_groups_to_lines([]) == []
    groups = ["ИС-21-1", "ИС-21-2", "ПИ-21-1", "МАТО-21-1"]
    lines = wrap_groups_to_lines(groups, max_chars=18)
    assert len(lines) >= 2
    for line in lines:
        assert len(line) <= 25


def test_layout_cell_content_normal_and_large_stream():
    # Empty slot
    assert layout_cell_content(None) is None
    assert layout_cell_content({"group": "   "}) is None

    # Normal slot (1 group)
    slot_single = {
        "group": "ИС-21-1 (105)",
        "subject": "Базы данных",
        "lessonType": "Лекция",
    }
    layout_single = layout_cell_content(slot_single, col_width=220)
    assert layout_single is not None
    assert layout_single["groups_list"] == ["ИС-21-1"]
    assert layout_single["room_str"] == "105"
    assert layout_single["lesson_type"] == "(Лекция)"
    assert layout_single["group_columns"] is None
    assert layout_single["estimated_height"] >= 100

    # Large stream slot (6 groups) triggers 2 columns
    slot_large = {
        "group": "Г1 (101), Г2 (101), Г3 (101), Г4 (101), Г5 (101), Г6 (101)",
        "subject": "Философия",
        "lessonType": "Лекция",
    }
    layout_large = layout_cell_content(slot_large, col_width=240)
    assert layout_large is not None
    assert len(layout_large["groups_list"]) == 6
    assert layout_large["group_columns"] is not None
    assert len(layout_large["group_columns"]["col1"]) == 3
    assert len(layout_large["group_columns"]["col2"]) == 3


def test_build_svg_structure_and_labels():
    service = TeacherTableImageService()

    schedule_data = [
        {
            "day": "Понедельник",
            "groups": [
                {
                    "time": "8.30-9.20",
                    "group": "ИС-21-1 (105)",
                    "subject": "Информатика",
                    "lessonType": "Лекция",
                }
            ],
        },
        {"day": "Вторник", "groups": []},
    ]

    svg_ru = service.build_svg({"fullName": "ст. преп. Жунусова А. К."}, schedule_data, lang="ru")
    assert "<svg" in svg_ru
    assert "Загруженность: Жунусова А. К." in svg_ru
    assert "Понедельник" in svg_ru
    assert "ИС-21-1" in svg_ru
    assert "Информатика" in svg_ru
    assert "(Лекция)" in svg_ru
    assert "День \\ Время" in svg_ru

    # Kazakh labels
    svg_kz = service.build_svg("доцент Ахметов Б. Т.", schedule_data, lang="kz")
    assert "Оқытушының жүктемесі: Ахметов Б. Т." in svg_kz
    assert "Күн \\ Уақыт" in svg_kz
    assert "сабақ" in svg_kz


def test_schedule_hash_and_file_id_caching():
    service = TeacherTableImageService()
    schedule_data = [
        {"day": "Понедельник", "groups": [{"time": "8.30-9.20", "group": "ИС-21-1"}]}
    ]

    h1 = service.compute_schedule_hash(schedule_data)
    h2 = service.compute_schedule_hash(schedule_data)
    assert h1 == h2
    assert len(h1) == 16

    assert service.get_cached_file_id(101, "ru", h1) is None
    service.cache_file_id(101, "ru", h1, "AgACAgIAAxkBAAI...")
    assert service.get_cached_file_id(101, "ru", h1) == "AgACAgIAAxkBAAI..."

    # Invalidate
    service.invalidate_teacher(101)
    assert service.get_cached_file_id(101, "ru", h1) is None


@pytest.mark.asyncio
async def test_render_table_image_png_output():
    service = TeacherTableImageService()
    schedule_data = [
        {
            "day": "Понедельник",
            "groups": [
                {
                    "time": "8.30-9.20",
                    "group": "ИС-21-1 (105)",
                    "subject": "Компьютерные сети",
                    "lessonType": "Прак",
                }
            ],
        }
    ]

    png_bytes = await service.render_table_image(
        {"fullName": "Жунусова А. К.", "id": 555},
        schedule_data,
        lang="ru",
    )

    assert isinstance(png_bytes, bytes)
    # Check PNG magic bytes header
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png_bytes) > 1000


@pytest.mark.asyncio
async def test_per_teacher_lock_concurrency():
    service = TeacherTableImageService()
    lock1 = await service.get_teacher_lock(999)
    lock2 = await service.get_teacher_lock(999)
    lock_other = await service.get_teacher_lock(888)

    assert lock1 is lock2
    assert lock1 is not lock_other


@pytest.mark.asyncio
async def test_table_image_cache_avoids_re_rendering():
    """Verifies that an existing cached file_id for the same schedule hash prevents re-rendering."""
    service = TeacherTableImageService()
    schedule_data = [
        {
            "day": "Понедельник",
            "groups": [
                {
                    "time": "8.30-9.20",
                    "group": "ИС-21-1 (105)",
                    "subject": "Компьютерные сети",
                    "lessonType": "Прак",
                }
            ],
        }
    ]
    sch_hash = service.compute_schedule_hash(schedule_data)
    teacher_id = 777
    lang = "ru"

    # Initially not cached
    assert service.get_cached_file_id(teacher_id, lang, sch_hash) is None

    # First render generates image and caches file_id
    with patch.object(service, "render_table_image", AsyncMock(return_value=b"fake_png")) as mock_render:
        service.cache_file_id(teacher_id, lang, sch_hash, "telegram_file_id_123")

        # When checking for cached_file_id
        cached_id = service.get_cached_file_id(teacher_id, lang, sch_hash)
        assert cached_id == "telegram_file_id_123"
        # Render was not called because file_id was retrieved from cache
        mock_render.assert_not_called()
