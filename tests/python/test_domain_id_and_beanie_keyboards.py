"""Tests for domain ID extraction and Beanie document keyboard integration.

Verifies that real Beanie model documents with MongoDB ObjectId primary keys
correctly produce numeric domain IDs in inline keyboard callback data,
preventing the regression where faculties and teachers were falling back to 0.
"""

from bson import ObjectId
import pytest

from src.bot.keyboards.inline import (
    get_departments_keyboard,
    get_domain_id,
    get_faculties_keyboard,
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
from src.core.i18n import t
from src.database.models.department import Department
from src.database.models.faculty import Faculty
from src.database.models.group import Group
from src.database.models.program import Program
from src.database.models.teacher import Teacher


def test_get_domain_id_with_dicts():
    # Integer ID
    assert get_domain_id({"id": 4, "name": "ФИТ"}) == 4
    # String digit ID
    assert get_domain_id({"id": "12", "name": "Кафедра"}) == 12
    # Specific attribute precedence
    assert get_domain_id({"faculty_id": 7, "id": 999}, "faculty_id") == 7
    # Hex ObjectId should return 0, not parse hex
    assert get_domain_id({"id": "660a123456789abcdef01234"}) == 0
    # Missing / empty
    assert get_domain_id({}) == 0
    assert get_domain_id(None) == 0


@pytest.mark.asyncio
async def test_beanie_models_produce_numeric_domain_ids(mock_db):
    """Test with real instantiated and saved Beanie documents."""
    f = Faculty(id=4, name="Факультет математики и ИТ")
    await f.insert()
    assert isinstance(f.id, ObjectId)  # Beanie's .id is ObjectId
    assert f.faculty_id == 4           # Domain ID is 4
    assert get_domain_id(f, "faculty_id") == 4
    assert get_domain_id(f) == 4

    p = Program(id=105, name="Информационные системы", faculty=4)
    await p.insert()
    assert isinstance(p.id, ObjectId)
    assert p.program_id == 105
    assert get_domain_id(p, "program_id") == 105
    assert get_domain_id(p) == 105

    g = Group(id=321, name="ИС-21-1", program=105, language="ru")
    await g.insert()
    assert isinstance(g.id, ObjectId)
    assert g.group_id == 321
    assert get_domain_id(g, "group_id") == 321
    assert get_domain_id(g) == 321

    d = Department(id=18, name="Кафедра прикладной математики")
    await d.insert()
    assert isinstance(d.id, ObjectId)
    assert d.department_id == 18
    assert get_domain_id(d, "department_id") == 18
    assert get_domain_id(d) == 18

    tch = Teacher(id=550, name="Жунусова А. К.", department=18)
    await tch.insert()
    assert isinstance(tch.id, ObjectId)
    assert tch.teacher_id == 550
    assert get_domain_id(tch, "teacher_id") == 550
    assert get_domain_id(tch) == 550


@pytest.mark.asyncio
async def test_keyboards_with_real_beanie_documents(mock_db):
    """Verifies all keyboard generators with real Beanie models produce clean numeric callbacks."""
    f = Faculty(id=5, name="Юридический факультет")
    await f.insert()
    kb_fac = get_faculties_keyboard([f], page=0, lang="ru")
    cb_fac = [btn.callback_data for row in kb_fac.inline_keyboard for btn in row]
    assert "program|5|0" in cb_fac
    # Verify no ObjectId hex appears in callback data
    assert not any(str(f.id) in cb for cb in cb_fac)

    p = Program(id=202, name="Юриспруденция", faculty=5)
    await p.insert()
    kb_prog = get_programs_keyboard([p], faculty_id=5, page=0, lang="ru")
    cb_prog = [btn.callback_data for row in kb_prog.inline_keyboard for btn in row]
    assert "group|5|202|0" in cb_prog
    assert not any(str(p.id) in cb for cb in cb_prog)

    g = Group(id=777, name="ЮР-21-2", program=202, language="kz")
    await g.insert()
    kb_grp = get_groups_keyboard([g], faculty_id=5, program_id=202, page=0, lang="kz", current_day=2)
    cb_grp = [btn.callback_data for row in kb_grp.inline_keyboard for btn in row]
    assert "chooseScheduleLanguage|kz|777|2" in cb_grp
    assert not any(str(g.id) in cb for cb in cb_grp)

    d = Department(id=30, name="Кафедра уголовного права")
    await d.insert()
    kb_dept = get_departments_keyboard([d], page=0, lang="ru")
    cb_dept = [btn.callback_data for row in kb_dept.inline_keyboard for btn in row]
    assert "teacher|30|0" in cb_dept
    assert not any(str(d.id) in cb for cb in cb_dept)

    tch = Teacher(id=990, name="Асанов Б. М.", department=30)
    await tch.insert()
    kb_tch = get_teachers_keyboard([tch], department_id=30, page=0, lang="ru")
    cb_tch = [btn.callback_data for row in kb_tch.inline_keyboard for btn in row]
    assert "teacherImg|990|0" in cb_tch
    assert not any(str(tch.id) in cb for cb in cb_tch)


def test_back_button_exact_text_without_duplicate_emoji():
    """Verifies back button text in all keyboards matches t('go_prev_menu') without '🔙 ' prefix."""
    for lang in ("ru", "kz"):
        expected_text = t("go_prev_menu", lang)
        assert not expected_text.startswith("🔙")
        assert "◀️" in expected_text

        # Faculties back button
        kb_f = get_faculties_keyboard([], lang=lang)
        assert kb_f.inline_keyboard[-1][0].text == expected_text

        # Programs back button
        kb_p = get_programs_keyboard([], faculty_id=1, lang=lang)
        assert kb_p.inline_keyboard[-1][0].text == expected_text

        # Groups back button
        kb_g = get_groups_keyboard([], faculty_id=1, program_id=1, lang=lang)
        assert kb_g.inline_keyboard[-1][0].text == expected_text

        # Departments back button
        kb_d = get_departments_keyboard([], lang=lang)
        assert kb_d.inline_keyboard[-1][0].text == expected_text

        # Teachers back button
        kb_t = get_teachers_keyboard([], department_id=1, lang=lang)
        assert kb_t.inline_keyboard[-1][0].text == expected_text

        # Schedule pagination back button
        kb_sch = get_schedule_pagination_keyboard("schedule|ru|100", 0, "faculty|0", lang=lang)
        assert kb_sch.inline_keyboard[1][0].text == expected_text

        # Teacher table back button
        kb_tt = get_teacher_table_keyboard(100, 0, 10, lang=lang)
        assert kb_tt.inline_keyboard[2][0].text == expected_text

        # Teacher text back button
        kb_tx = get_teacher_text_keyboard(100, 0, 10, lang=lang)
        assert kb_tx.inline_keyboard[2][0].text == expected_text

        # Search results back button
        kb_sr = get_search_results_keyboard([], [], lang=lang)
        assert kb_sr.inline_keyboard[-1][0].text == expected_text


def test_get_domain_id_camel_case_and_floats():
    """Verifies that camelCase keys and whole float IDs are safely parsed into domain integers."""
    # camelCase dicts
    assert get_domain_id({"groupId": 101}, "group_id") == 101
    assert get_domain_id({"groupId": 101}) == 101
    assert get_domain_id({"teacherId": 202}, "teacher_id") == 202
    assert get_domain_id({"teacherId": 202}) == 202
    assert get_domain_id({"departmentId": 303}, "department_id") == 303
    assert get_domain_id({"departmentId": 303}) == 303
    assert get_domain_id({"facultyId": 404}, "faculty_id") == 404
    assert get_domain_id({"facultyId": 404}) == 404
    assert get_domain_id({"programId": 505}, "program_id") == 505
    assert get_domain_id({"programId": 505}) == 505

    # Whole float domain ID
    assert get_domain_id({"id": 77.0}) == 77
    assert get_domain_id({"faculty_id": 88.0}, "faculty_id") == 88

    # Non-integer float returns 0
    assert get_domain_id({"id": 77.5}) == 0

    # camelCase on arbitrary object
    class CamelObj:
        def __init__(self, val):
            self.groupId = val
    assert get_domain_id(CamelObj(999), "group_id") == 999
    assert get_domain_id(CamelObj(999)) == 999


def test_teacher_keyboards_back_button_targets():
    """Verifies that teacher keyboards return to department|0 when department_id is 0."""
    # When department is known
    kb_table_known = get_teacher_table_keyboard(55, 0, department_id=12, lang="ru")
    assert kb_table_known.inline_keyboard[2][0].callback_data == "teacher|12|0"

    kb_text_known = get_teacher_text_keyboard(55, 0, department_id=12, lang="ru")
    assert kb_text_known.inline_keyboard[2][0].callback_data == "teacher|12|0"

    # When department is 0 / unknown (e.g. from search or /teacher)
    kb_table_zero = get_teacher_table_keyboard(55, 0, department_id=0, lang="ru")
    assert kb_table_zero.inline_keyboard[2][0].callback_data == "department|0"

    kb_text_zero = get_teacher_text_keyboard(55, 0, department_id=0, lang="ru")
    assert kb_text_zero.inline_keyboard[2][0].callback_data == "department|0"

