"""Tests for export database clarity and catalog vs schedule cache distinction."""

from __future__ import annotations

import pytest
from src.database.models.group import Group
from src.database.models.teacher import Teacher
from src.database.models.schedule import Schedule
from src.database.models.teacher_schedule import TeacherSchedule


@pytest.mark.asyncio
async def test_catalog_groups_distinct_from_schedules_cache(mock_db):
    """Verifies that catalog groups and schedule cache are completely isolated.
    
    A university catalog contains ~1314 groups.
    The schedule collection contains cached timetable grids (e.g. 976 schedules).
    Adding or updating a schedule cache NEVER changes catalog groups.
    """
    # 1. Seed 3 catalog groups
    await Group(id=6337, name="КО-24-2к").insert()
    await Group(id=6346, name="КИ-24-1к").insert()
    await Group(id=6553, name="ТЭ-24-1к").insert()

    assert await Group.count() == 3
    assert await Schedule.count() == 0

    # 2. Add 1 schedule to cache
    await Schedule(
        groupId=6337,
        data=[{"day": "Понедельник", "subjects": [{"time": "8.30-9.20", "subject": "Математика"}]}],
        hasSchedule=True,
    ).insert()

    # Schedule cache has 1 item, catalog still has exactly 3 groups
    assert await Schedule.count() == 1
    assert await Group.count() == 3

    # 3. Add schedule for another group
    await Schedule(
        groupId=6346,
        data=[],
        hasSchedule=False,
    ).insert()

    assert await Schedule.count() == 2
    assert await Group.count() == 3


@pytest.mark.asyncio
async def test_catalog_teachers_distinct_from_teacher_schedules_cache(mock_db):
    """Verifies that catalog teachers and teacher schedule cache are completely isolated.
    
    Catalog teachers (~1413) exist in 'teachers'.
    Cached timetables (~728) exist in 'teacherschedules'.
    """
    await Teacher(id=8, name="Додонова А. Ш.").insert()
    await Teacher(id=4414, name="Народхан Дашжан").insert()

    assert await Teacher.count() == 2
    assert await TeacherSchedule.count() == 0

    await TeacherSchedule(
        teacherId=8,
        data=[{"day": "Понедельник", "groups": [{"time": "8.30-9.20", "group": "КО-24-2к", "subject": "Физика"}]}],
    ).insert()

    assert await TeacherSchedule.count() == 1
    assert await Teacher.count() == 2


def test_export_script_clarity_labels():
    """Verifies that scratch/export_database_to_drive.py clearly separates catalog from cache in output strings."""
    with open("scratch/export_database_to_drive.py", "r", encoding="utf-8") as f:
        content = f.read()

    # Must contain clear descriptions of collections
    assert "коллекция 'schedules'" in content
    assert "коллекция 'teacherschedules'" in content
    assert "сохраненных расписаний групп" in content
    assert "сохраненных расписаний преподавателей" in content
    assert "СВОДНЫЙ ОТЧЁТ ВЫГРУЗКИ БАЗЫ ДАННЫХ KICHOBOT:" in content
    assert "Официальный каталог КарУ (groups):" in content
    assert "Официальный каталог КарУ (teachers):" in content
    assert "не создаёт новые группы или преподавателей в каталоге!" in content


@pytest.mark.asyncio
async def test_schedule_update_does_not_modify_group_attributes(mock_db):
    """Verifies that changing timetable data in Schedule never mutates Group attributes."""
    g = Group(id=6337, name="КО-24-2к", language="каз", studentCount=18)
    await g.insert()

    s = Schedule(groupId=6337, data=[], hasSchedule=False)
    await s.insert()

    # Update schedule with 5 lessons
    s.data = [{"day": "Вторник", "subjects": [{"time": "10.00-10.50", "subject": "Информатика"}]}]
    s.has_schedule = True
    await s.save()

    refetched_g = await Group.find_one(Group.group_id == 6337)
    assert refetched_g is not None
    assert refetched_g.name == "КО-24-2к"
    assert refetched_g.language == "каз"
    assert refetched_g.student_count == 18

