"""Unit tests for Phase 5 Keyboards: inline navigation and selection markups."""

from aiogram.types import InlineKeyboardMarkup

from src.bot.keyboards.inline import (
    get_departments_keyboard,
    get_faculties_keyboard,
    get_fuzzy_suggestions_keyboard,
    get_groups_keyboard,
    get_language_keyboard,
    get_new_schedule_keyboard,
    get_programs_keyboard,
    get_schedule_pagination_keyboard,
    get_search_results_keyboard,
    get_teacher_table_keyboard,
    get_teacher_text_keyboard,
    get_teachers_keyboard,
)


def test_language_keyboard():
    kb = get_language_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[0][0].callback_data == "languageIsRu"
    assert kb.inline_keyboard[0][0].text == "Русский"
    assert kb.inline_keyboard[1][0].callback_data == "languageIsKz"
    assert kb.inline_keyboard[1][0].text == "Қазақ"
    assert kb.inline_keyboard[2][1].callback_data == "delete"
    assert kb.inline_keyboard[2][1].text == "❌"


def test_new_schedule_keyboard():
    kb = get_new_schedule_keyboard(lang="ru")
    assert isinstance(kb, InlineKeyboardMarkup)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "faculty|0" in callbacks
    assert "department|0" in callbacks
    assert "delete" in callbacks
    assert "nothing" in callbacks


def test_faculties_keyboard_single_and_multi_page():
    # Single page (3 faculties)
    faculties = [
        {"id": 1, "name": "Физико-технический"},
        {"id": 2, "name": "Математики и ИТ"},
        {"id": 3, "name": "Исторический"},
    ]
    kb_single = get_faculties_keyboard(faculties, page=0, lang="ru")
    callbacks = [btn.callback_data for row in kb_single.inline_keyboard for btn in row]
    assert "program|1|0" in callbacks
    assert "program|2|0" in callbacks
    assert "program|3|0" in callbacks
    assert "start" in callbacks
    # No pagination row since page_count == 0
    assert "nothing" not in callbacks

    # Multi page (15 faculties)
    faculties_15 = [{"id": i, "name": f"Факультет {i}"} for i in range(1, 16)]
    kb_multi = get_faculties_keyboard(faculties_15, page=0, lang="ru")
    callbacks_multi = [btn.callback_data for row in kb_multi.inline_keyboard for btn in row]
    assert "nothing" in callbacks_multi
    assert "faculty|1" in callbacks_multi  # next page
    assert "faculty|1" in callbacks_multi  # prev page wraps to page 1 (since 2 pages total: 0 and 1)


def test_programs_keyboard():
    programs = [
        {"id": 101, "name": "Информационные системы"},
        {"id": 102, "name": "Прикладная математика"},
    ]
    kb = get_programs_keyboard(programs, faculty_id=2, page=0, lang="ru")
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "group|2|101|0" in callbacks
    assert "group|2|102|0" in callbacks
    assert "faculty|0" in callbacks


def test_groups_keyboard():
    groups = [
        {"id": 501, "name": "ИС-21-1", "language": "ru"},
        {"id": 502, "name": "ИС-21-2", "language": "kz"},
    ]
    kb = get_groups_keyboard(groups, faculty_id=2, program_id=101, page=0, lang="ru", current_day=1)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "chooseScheduleLanguage|ru|501|1" in callbacks
    assert "chooseScheduleLanguage|kz|502|1" in callbacks
    assert "program|2|0" in callbacks


def test_departments_and_teachers_keyboards():
    departments = [{"id": 10, "name": "Кафедра прикладной математики"}]
    kb_dept = get_departments_keyboard(departments, page=0, lang="ru")
    cb_dept = [btn.callback_data for row in kb_dept.inline_keyboard for btn in row]
    assert "teacher|10|0" in cb_dept
    assert "start" in cb_dept

    teachers = [{"id": 305, "fullName": "Жунусова А. К."}]
    kb_tch = get_teachers_keyboard(teachers, department_id=10, page=0, lang="ru")
    cb_tch = [btn.callback_data for row in kb_tch.inline_keyboard for btn in row]
    assert any(cb.startswith("TeacherSchedule|305|") for cb in cb_tch)
    assert "department|0" in cb_tch


def test_schedule_pagination_keyboard():
    # Day 0 (Monday): prev day should wrap to 5 (Saturday), next day is 1 (Tuesday)
    kb_day0 = get_schedule_pagination_keyboard("schedule|ru|501", day_number=0, back_callback="faculty|0")
    row0 = kb_day0.inline_keyboard[0]
    assert row0[0].callback_data == "schedule|ru|501|5"
    assert row0[1].callback_data == "refreshschedule|ru|501|0"
    assert row0[2].callback_data == "schedule|ru|501|1"
    assert kb_day0.inline_keyboard[1][0].callback_data == "faculty|0"

    # Day 5 (Saturday): next day should wrap to 0 (Monday)
    kb_day5 = get_schedule_pagination_keyboard("schedule|ru|501", day_number=5, back_callback="start")
    row5 = kb_day5.inline_keyboard[0]
    assert row5[0].callback_data == "schedule|ru|501|4"
    assert row5[1].callback_data == "refreshschedule|ru|501|5"
    assert row5[2].callback_data == "schedule|ru|501|0"


def test_teacher_table_and_text_keyboards():
    # Table image controls
    kb_table = get_teacher_table_keyboard(teacher_id=305, day_number=2, department_id=10, lang="ru")
    cb_table = [btn.callback_data for row in kb_table.inline_keyboard for btn in row]
    assert "teacherText|305|2" in cb_table
    assert "refreshteacherImg|305|2" in cb_table
    assert "teacher|10|0" in cb_table

    # Text daily view controls: row 0 = navigation [◀️ | 🔄 | ▶️], row 1 = table button, row 2 = back
    kb_text = get_teacher_text_keyboard(teacher_id=305, day_number=2, department_id=10, lang="ru")
    assert len(kb_text.inline_keyboard) == 3

    # Row 0: Navigation
    assert len(kb_text.inline_keyboard[0]) == 3
    assert kb_text.inline_keyboard[0][0].callback_data == "TeacherSchedule|305|1"
    assert kb_text.inline_keyboard[0][1].callback_data == "refreshTeacherSchedule|305|2"
    assert kb_text.inline_keyboard[0][2].callback_data == "TeacherSchedule|305|3"

    # Row 1: Week table button
    assert len(kb_text.inline_keyboard[1]) == 1
    assert kb_text.inline_keyboard[1][0].callback_data == "teacherImg|305|2"

    # Row 2: Back button
    assert len(kb_text.inline_keyboard[2]) == 1
    assert kb_text.inline_keyboard[2][0].callback_data == "teacher|10|0"


def test_search_results_and_fuzzy_keyboards():
    groups = [{"id": 501, "name": "ИС-21-1", "language": "ru"}]
    teachers = [{"id": 305, "fullName": "Жунусова А. К."}]

    kb_search = get_search_results_keyboard(groups=groups, teachers=teachers, lang="ru", current_day=0)
    cb_search = [btn.callback_data for row in kb_search.inline_keyboard for btn in row]
    assert "chooseScheduleLanguage|ru|501|0" in cb_search
    assert "TeacherSchedule|305|0" in cb_search
    assert "start" in cb_search

    suggestions = [
        {"type": "group", "item": {"id": 502, "name": "ИС-21-2", "language": "ru"}},
        {"type": "teacher", "item": {"id": 306, "fullName": "Попова Н. В."}},
    ]
    kb_fuzzy = get_fuzzy_suggestions_keyboard(suggestions, lang="ru", current_day=0)
    cb_fuzzy = [btn.callback_data for row in kb_fuzzy.inline_keyboard for btn in row]
    assert "chooseScheduleLanguage|ru|502|0" in cb_fuzzy
    assert "TeacherSchedule|306|0" in cb_fuzzy
    assert "start" in cb_fuzzy

    # Test category tabs and pagination
    many_groups = [{"id": 500 + i, "name": f"ИС-2{i}", "language": "ru"} for i in range(12)]
    many_teachers = [{"id": 300 + i, "fullName": f"Препод {i}"} for i in range(10)]

    # Tab "grp" with page 0 (8 items per page)
    kb_grp = get_search_results_keyboard(groups=many_groups, teachers=many_teachers, tab="grp", page=0, query="ис", page_size=8)
    cb_grp = [btn.callback_data for row in kb_grp.inline_keyboard for btn in row]
    assert "srch|g|1|ис" in cb_grp  # Next page button
    assert "srch|t|0|ис" in cb_grp  # Switch to teachers tab

    # Tab "tch" with page 1
    kb_tch_tab = get_search_results_keyboard(groups=many_groups, teachers=many_teachers, tab="tch", page=1, query="ис", page_size=8)
    cb_tch_tab = [btn.callback_data for row in kb_tch_tab.inline_keyboard for btn in row]
    assert "srch|t|0|ис" in cb_tch_tab  # Prev page button

