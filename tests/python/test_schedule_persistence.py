"""Unit tests for background schedule persistence into MongoDB."""

import pytest

from src.bot.handlers.callbacks_router import (
    _bg_persist_student_schedule,
    _bg_persist_teacher_schedule,
)
from src.database.models.schedule import Schedule
from src.database.models.teacher_schedule import TeacherSchedule


@pytest.mark.asyncio
async def test_bg_persist_student_schedule(mock_db):
    """Verifies that fresh student schedule is saved and updated in Schedule collection."""
    group_id = 1234
    data_v1 = [{"day": "Понедельник", "subjects": [{"subject": "Math", "time": "08:30"}]}]

    await _bg_persist_student_schedule(group_id, data_v1)

    doc = await Schedule.find_one({"groupId": group_id})
    assert doc is not None
    assert doc.group_id == group_id
    assert len(doc.data) == 1
    assert doc.data[0].day == "Понедельник"
    initial_hash = doc.hash

    # Update with new data
    data_v2 = [
        {"day": "Понедельник", "subjects": [{"subject": "Math", "time": "08:30"}]},
        {"day": "Вторник", "subjects": [{"subject": "Physics", "time": "10:00"}]},
    ]
    await _bg_persist_student_schedule(group_id, data_v2)

    updated_doc = await Schedule.find_one({"groupId": group_id})
    assert updated_doc is not None
    assert len(updated_doc.data) == 2
    assert updated_doc.hash != initial_hash


@pytest.mark.asyncio
async def test_bg_persist_teacher_schedule(mock_db):
    """Verifies that fresh teacher schedule is saved and updated in TeacherSchedule collection."""
    teacher_id = 5678
    data_v1 = [{"day": "Среда", "groups": [{"group": "ИС-26-1", "time": "09:00"}]}]

    await _bg_persist_teacher_schedule(teacher_id, data_v1)

    doc = await TeacherSchedule.find_one({"teacherId": teacher_id})
    assert doc is not None
    assert doc.teacher_id == teacher_id
    assert len(doc.data) == 1
    assert doc.data[0].day == "Среда"

    # Update
    data_v2 = [{"day": "Среда", "groups": [{"group": "ИС-26-1", "time": "09:00"}, {"group": "ИС-25-1", "time": "10:30"}]}]
    await _bg_persist_teacher_schedule(teacher_id, data_v2)

    updated_doc = await TeacherSchedule.find_one({"teacherId": teacher_id})
    assert updated_doc is not None
    assert len(updated_doc.data[0].groups) == 2
