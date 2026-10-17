"""Unit tests for ScheduleApiAdapter."""

import pytest
from src.services.schedule_adapter import schedule_api_adapter


def test_normalize_time():
    """Verifies time string normalization."""
    assert schedule_api_adapter.normalize_time("08:30-09:20") == "8.30-9.20"
    assert schedule_api_adapter.normalize_time("10:00 - 10:50") == "10.00-10.50"
    assert schedule_api_adapter.normalize_time("08:00–09:30") == "8.00-9.30"
    assert schedule_api_adapter.normalize_time("") == ""
    assert schedule_api_adapter.normalize_time(None) == ""


def test_format_room():
    """Verifies classroom and building formatting."""
    assert schedule_api_adapter.format_room("315", "3") == "Ауд.315/3"
    assert schedule_api_adapter.format_room("204", "") == "Ауд.204"
    assert schedule_api_adapter.format_room("Ауд. 204", "Корпус 1") == "Ауд.204/1"
    assert schedule_api_adapter.format_room("", "") == ""
    assert schedule_api_adapter.format_room(None, None) == ""


def test_format_lesson_type():
    """Verifies lesson type badge formatting with slashes and plain badges."""
    assert schedule_api_adapter.format_lesson_type("лекция") == "/Лекция/"
    assert schedule_api_adapter.format_lesson_type("практика") == "/Прак.зан./"
    assert schedule_api_adapter.format_lesson_type("лаб.работа") == "/Лаб.раб./"
    assert schedule_api_adapter.format_lesson_type("сроп") == "/СРОП/"
    assert schedule_api_adapter.format_lesson_type("") == ""

    assert schedule_api_adapter.format_lesson_type_badge("лекция") == "Лекция"
    assert schedule_api_adapter.format_lesson_type_badge("семинар") == "Прак.зан."
    assert schedule_api_adapter.format_lesson_type_badge("лабораторная") == "Лаб.раб."
    assert schedule_api_adapter.format_lesson_type_badge("сроп") == "СРОП"


def test_adapt_group_schedule_russian():
    """Verifies student group schedule generation in Russian."""
    raw_records = [
        {
            "dayIndex": 1,
            "time": "08:30-09:20",
            "slot": 1,
            "subject": "Математика",
            "type": "лекция",
            "teacher": "Попова Н. В.",
            "room": "315",
            "building": "1",
        },
        {
            "dayIndex": 1,
            "time": "09:30-10:20",
            "slot": 2,
            "subject": "Физика",
            "type": "практика",
            "teacher": "Нигай Е. В.",
            "room": "102",
            "building": "1",
        },
    ]

    schedule = schedule_api_adapter.adapt_group_schedule(raw_records, "рус")
    assert len(schedule) == 6
    assert schedule[0]["day"] == "Понедельник"
    assert len(schedule[0]["subjects"]) == 2
    assert "Математика" in schedule[0]["subjects"][0]["subject"]
    assert "Физика" in schedule[0]["subjects"][1]["subject"]
    assert schedule[0]["subjects"][0]["time"] == "8.30-9.20"


def test_adapt_group_schedule_subgroups_with_book_symbol():
    """Verifies that multiple subgroups in same time slot are joined with book emoji."""
    raw_records = [
        {
            "dayIndex": 1,
            "time": "10:30-11:20",
            "slot": 3,
            "subject": "Информатика",
            "type": "лаб",
            "teacher": "Попова Н. В.",
            "room": "201",
            "building": "3",
        },
        {
            "dayIndex": 1,
            "time": "10:30-11:20",
            "slot": 3,
            "subject": "Информатика",
            "type": "лаб",
            "teacher": "Нигай Е. В.",
            "room": "202",
            "building": "3",
        },
    ]

    schedule = schedule_api_adapter.adapt_group_schedule(raw_records, "рус")
    mon = schedule[0]
    assert len(mon["subjects"]) == 1
    assert "📚" in mon["subjects"][0]["subject"]
    assert "201/3" in mon["subjects"][0]["subject"]
    assert "202/3" in mon["subjects"][0]["subject"]


def test_adapt_group_schedule_kazakh_days():
    """Verifies Kazakh localization of day names."""
    raw_records = [
        {
            "dayIndex": 1,
            "time": "08:30-09:20",
            "slot": 1,
            "subject": "Математика",
            "type": "лекция",
            "teacher": "Попова Н. В.",
            "room": "315",
            "building": "1",
        }
    ]

    for lang_code in ("kz", "kaz", "қаз", "KZ"):
        schedule = schedule_api_adapter.adapt_group_schedule(raw_records, lang_code)
        assert len(schedule) == 6
        assert schedule[0]["day"] == "Дүйсенбі"
        assert schedule[1]["day"] == "Сейсенбі"
        assert schedule[2]["day"] == "Сәрсенбі"
        assert schedule[3]["day"] == "Бейсенбі"
        assert schedule[4]["day"] == "Жұма"
        assert schedule[5]["day"] == "Сенбі"


def test_adapt_teacher_schedule_streaming_groups():
    """Verifies teacher schedule merges multiple groups in the same time slot."""
    raw_records = [
        {
            "dayIndex": 2,
            "time": "10:30-11:20",
            "slot": 3,
            "subject": "Базы данных",
            "type": "лекция",
            "group": "ИТ-21-1",
            "room": "210",
            "building": "3",
        },
        {
            "dayIndex": 2,
            "time": "10:30-11:20",
            "slot": 3,
            "subject": "Базы данных",
            "type": "лекция",
            "group": "ИС-21-2",
            "room": "210",
            "building": "3",
        },
    ]

    schedule = schedule_api_adapter.adapt_teacher_schedule(raw_records, "рус")
    assert len(schedule) == 6
    tue = schedule[1]
    assert tue["day"] == "Вторник"
    assert len(tue["groups"]) == 1
    slot = tue["groups"][0]
    assert "ИТ-21-1, ИС-21-2" in slot["group"]
    assert "210/3" in slot["group"]
    assert slot["subject"] == "Базы данных"
    assert slot["lessonType"] == "Лекция"


def test_adapt_teacher_schedule_kazakh_days():
    """Verifies Kazakh localization of day names for teacher schedules."""
    raw_records = [
        {
            "dayIndex": 1,
            "time": "08:30-09:20",
            "slot": 1,
            "subject": "Физика",
            "type": "лекция",
            "group": "ФИЗ-21",
            "room": "101",
            "building": "1",
        }
    ]

    schedule = schedule_api_adapter.adapt_teacher_schedule(raw_records, "kz")
    assert len(schedule) == 6
    assert schedule[0]["day"] == "Дүйсенбі"
    assert len(schedule[0]["groups"]) == 1


def test_format_room_deduplication():
    """Verifies that format_room does not duplicate building suffixes."""
    assert schedule_api_adapter.format_room("315/1", "1") == "Ауд.315/1"
    assert schedule_api_adapter.format_room("315/1", 1) == "Ауд.315/1"
    assert schedule_api_adapter.format_room("315/2", "Корпус 2") == "Ауд.315/2"
    assert schedule_api_adapter.format_room("315", "1") == "Ауд.315/1"
    assert schedule_api_adapter.format_room("Ауд. 204/3", "3") == "Ауд.204/3"


def test_get_day_index_string_and_names():
    """Verifies get_day_index handles integers, numeric strings, and localized day names."""
    assert schedule_api_adapter.get_day_index({"dayIndex": "1"}) == 1
    assert schedule_api_adapter.get_day_index({"dayIndex": "5"}) == 5
    assert schedule_api_adapter.get_day_index({"dayIndex": 3}) == 3
    assert schedule_api_adapter.get_day_index({"day": "Понедельник"}) == 1
    assert schedule_api_adapter.get_day_index({"day": "Жұма"}) == 5
    assert schedule_api_adapter.get_day_index({}) == 0


def test_adapt_teacher_schedule_streaming_retains_room():
    """Verifies teacher schedule merges groups and retains room when subsequent record has blank room."""
    raw_records = [
        {
            "dayIndex": 1,
            "time": "08:30-09:20",
            "slot": 1,
            "subject": "Электроника",
            "type": "лекция",
            "group": "РЭТ-21-1",
            "room": "205",
            "building": "2",
        },
        {
            "dayIndex": 1,
            "time": "08:30-09:20",
            "slot": 1,
            "subject": "Электроника",
            "type": "лекция",
            "group": "РЭТ-21-2",
            "room": "",
            "building": "",
        },
    ]

    schedule = schedule_api_adapter.adapt_teacher_schedule(raw_records, "рус")
    mon = schedule[0]
    assert len(mon["groups"]) == 1
    group_entry = mon["groups"][0]
    assert "РЭТ-21-1, РЭТ-21-2" in group_entry["group"]
    assert "205/2" in group_entry["group"]


def test_localize_schedule_days():
    """Verifies localize_schedule_days translates day names between RU and KZ."""
    days_ru = [
        {"day": "Понедельник", "subjects": []},
        {"day": "Среда", "subjects": []},
    ]
    localized_kz = schedule_api_adapter.localize_schedule_days(days_ru, "kz")
    assert localized_kz[0]["day"] == "Дүйсенбі"
    assert localized_kz[1]["day"] == "Сәрсенбі"

    localized_ru = schedule_api_adapter.localize_schedule_days(localized_kz, "ru")
    assert localized_ru[0]["day"] == "Понедельник"
    assert localized_ru[1]["day"] == "Среда"


def test_format_lesson_type_kazakh_terms():
    """Verifies that Kazakh lesson type terms are properly normalized."""
    assert schedule_api_adapter.format_lesson_type("дәріс") == "/Лекция/"
    assert schedule_api_adapter.format_lesson_type("тәжірибелік сабақ") == "/Прак.зан./"
    assert schedule_api_adapter.format_lesson_type("зертханалық сабақ") == "/Лаб.раб./"
    assert schedule_api_adapter.format_lesson_type("СОӨЖ") == "/СРОП/"
    assert schedule_api_adapter.format_lesson_type("/Лекция/") == "/Лекция/"

    assert schedule_api_adapter.format_lesson_type_badge("дәріс") == "Лекция"
    assert schedule_api_adapter.format_lesson_type_badge("тәжірибе") == "Прак.зан."
    assert schedule_api_adapter.format_lesson_type_badge("зертхана") == "Лаб.раб."
    assert schedule_api_adapter.format_lesson_type_badge("соөж") == "СРОП"


def test_adapt_group_schedule_exact_duplicate_deduplication():
    """Verifies that identical duplicate lesson blocks in the same slot are deduplicated."""
    raw_records = [
        {
            "dayIndex": 1,
            "time": "08:30-09:20",
            "slot": 1,
            "subject": "Физика",
            "type": "лекция",
            "teacher": "Попова Н. В.",
            "room": "315",
            "building": "1",
        },
        {
            "dayIndex": 1,
            "time": "08:30-09:20",
            "slot": 1,
            "subject": "Физика",
            "type": "лекция",
            "teacher": "Попова Н. В.",
            "room": "315",
            "building": "1",
        },
    ]
    schedule = schedule_api_adapter.adapt_group_schedule(raw_records, "рус")
    mon = schedule[0]
    assert len(mon["subjects"]) == 1
    assert "📚" not in mon["subjects"][0]["subject"]


def test_adapt_teacher_schedule_no_double_building_slash():
    """Verifies that room already containing building slash is not duplicated into e.g. 305/1/1."""
    raw_records = [
        {
            "dayIndex": 1,
            "time": "08:30-09:20",
            "slot": 1,
            "subject": "Информатика",
            "type": "лекция",
            "group": "ИС-21",
            "room": "305/1",
            "building": "1",
        }
    ]
    schedule = schedule_api_adapter.adapt_teacher_schedule(raw_records, "рус")
    group_str = schedule[0]["groups"][0]["group"]
    assert "305/1/1" not in group_str
    assert "305/1" in group_str


def test_adapt_teacher_schedule_preformatted_group_room_deduplication():
    """Verifies that pre-formatted group strings containing classroom do not duplicate room string."""
    raw_records = [
        {
            "dayIndex": 1,
            "time": "10:00-11:20",
            "slot": 2,
            "subject": "Базы данных",
            "type": "лекция",
            "group": "ИС-21-1 (Ауд. 305/1)",
            "room": "305",
            "building": "1",
        }
    ]
    schedule = schedule_api_adapter.adapt_teacher_schedule(raw_records, "рус")
    group_str = schedule[0]["groups"][0]["group"]
    assert "(Ауд. 305/1) (305/1)" not in group_str
    assert group_str == "ИС-21-1 (305/1)"


