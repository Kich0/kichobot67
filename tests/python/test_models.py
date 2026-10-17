"""Unit tests for Beanie models and Mongoose schema compatibility."""

from datetime import datetime, timezone
import pytest
from bson import ObjectId
from src.database.models import (
    User,
    Group,
    Teacher,
    Faculty,
    Program,
    Department,
    TeacherProfile,
    Schedule,
    ScheduleDay,
    SubjectItem,
    TeacherSchedule,
    TeacherScheduleDay,
    TeacherLessonItem,
    UserAction,
    BlackList,
    Log,
    UserRegistrationStat,
    UserActivity,
    UserToll,
    ScheduleVersion,
    TeacherScheduleVersion,
)


@pytest.mark.asyncio
async def test_user_model_mongoose_compatibility(mock_db):
    """Verifies that User model loads Mongoose document with __v and camelCase fields."""
    raw_mongoose_doc = {
        "_id": ObjectId("651234567890abcdef123456"),
        "userId": 123456789,
        "username": "test_student",
        "firstName": "Алихан",
        "lastName": "Сериков",
        "group": 101,
        "teacher": None,
        "scheduleType": "group",
        "isAdmin": False,
        "language": "kz",
        "__v": 3,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }

    # Insert raw document into MongoDB
    await mock_db["users"].insert_one(raw_mongoose_doc)

    # Read via Beanie
    user = await User.find_one(User.user_id == 123456789)
    assert user is not None
    assert user.user_id == 123456789
    assert user.username == "test_student"
    assert user.first_name == "Алихан"
    assert user.last_name == "Сериков"
    assert user.group == 101
    assert user.language == "kz"
    assert user.is_admin is False
    assert user.id == ObjectId("651234567890abcdef123456")


@pytest.mark.asyncio
async def test_group_model_crud(mock_db):
    """Verifies Group creation and query by item_id / name."""
    group = Group(
        id=55,
        name="ПИ-21-1",
        language="ru",
        program=12,
        studentCount=25,
    )
    await group.insert()

    found = await Group.find_one(Group.item_id == 55)
    assert found is not None
    assert found.name == "ПИ-21-1"
    assert found.student_count == 25
    assert found.group_id == 55

    # Raw collection verification
    raw = await mock_db["groups"].find_one({"id": 55})
    assert raw["name"] == "ПИ-21-1"
    assert raw["id"] == 55


@pytest.mark.asyncio
async def test_teacher_model_crud(mock_db):
    """Verifies Teacher document persistence and fields."""
    teacher = Teacher(
        id=99,
        name="Иванов И.И.",
        href="http://example.com/teacher/99",
        department=7,
        fullName="Иванов Иван Иванович",
        jobTitle="доцент",
    )
    await teacher.insert()

    found = await Teacher.find_one(Teacher.item_id == 99)
    assert found is not None
    assert found.full_name == "Иванов Иван Иванович"
    assert found.department == 7
    assert found.job_title == "доцент"


@pytest.mark.asyncio
async def test_schedule_model_with_nested_days(mock_db):
    """Verifies Schedule model with nested ScheduleDay and SubjectItem structures."""
    schedule = Schedule(
        groupId=101,
        hash="abc123hash",
        data=[
            ScheduleDay(
                day="Понедельник",
                subjects=[
                    SubjectItem(time="08:30-09:20", subject="Высшая математика"),
                    SubjectItem(time="09:30-10:20", subject="Физика"),
                ],
            )
        ],
    )
    await schedule.insert()

    found = await Schedule.find_one(Schedule.group_id == 101)
    assert found is not None
    assert len(found.data) == 1
    assert found.data[0].day == "Понедельник"
    assert len(found.data[0].subjects) == 2
    assert found.data[0].subjects[0].subject == "Высшая математика"


@pytest.mark.asyncio
async def test_teacher_schedule_model(mock_db):
    """Verifies TeacherSchedule model with nested TeacherScheduleDay structures."""
    ts = TeacherSchedule(
        teacherId=99,
        hash="teacherhash456",
        data=[
            TeacherScheduleDay(
                day="Вторник",
                groups=[
                    TeacherLessonItem(
                        time="10:30-11:20",
                        group="ПИ-21-1",
                        subject="Базы данных",
                        lessonType="Лекция",
                    )
                ],
            )
        ],
    )
    await ts.insert()

    found = await TeacherSchedule.find_one(TeacherSchedule.teacher_id == 99)
    assert found is not None
    assert len(found.data) == 1
    assert found.data[0].groups[0].group == "ПИ-21-1"
    assert found.data[0].groups[0].lesson_type == "Лекция"


@pytest.mark.asyncio
async def test_user_action_model(mock_db):
    """Verifies UserAction logging document."""
    action = UserAction(
        userId=123,
        username="student1",
        action="search_group",
        text="ПИ-21-1",
        isFirst=True,
    )
    await action.insert()

    found = await UserAction.find_one(UserAction.user_id == 123)
    assert found is not None
    assert found.action == "search_group"
    assert found.text == "ПИ-21-1"
    assert found.is_first is True


@pytest.mark.asyncio
async def test_blacklist_model(mock_db):
    """Verifies BlackList document creation and query."""
    bl = BlackList(
        userId=777,
        type="ban",
        reason="Spamming requests",
    )
    await bl.insert()

    found = await BlackList.find_one(BlackList.user_id == 777)
    assert found is not None
    assert found.type == "ban"
    assert found.reason == "Spamming requests"


@pytest.mark.asyncio
async def test_extra_and_legacy_fields_ignored(mock_db):
    """Verifies that unknown legacy fields in MongoDB do not cause validation errors."""
    raw = {
        "_id": ObjectId(),
        "userId": 999999,
        "legacyField1": "old_data",
        "nestedOld": {"key": "val"},
        "__v": 0,
        "unknownList": [1, 2, 3],
    }
    await mock_db["users"].insert_one(raw)
    loaded = await User.find_one(User.user_id == 999999)
    assert loaded is not None
    assert loaded.user_id == 999999


@pytest.mark.asyncio
async def test_kazakh_cyrillic_characters_support(mock_db):
    """Verifies support for Kazakh letters (Ә, І, Ң, Ғ, Ү, Ұ, Қ, Ө, һ)."""
    kz_name = "Әбілқасым Сағынұлы Құнанбаев"
    teacher = Teacher(
        id=701,
        name=kz_name,
        department=3,
        fullName=kz_name,
    )
    await teacher.insert()

    loaded = await Teacher.find_one(Teacher.item_id == 701)
    assert loaded is not None
    assert loaded.full_name == kz_name


@pytest.mark.asyncio
async def test_all_auxiliary_models_crud(mock_db):
    """Verifies CRUD operations for all auxiliary models."""
    # Faculty
    fac = Faculty(id=1, name="Математика және ақпараттық технологиялар факультеті")
    await fac.insert()
    assert (await Faculty.find_one(Faculty.item_id == 1)) is not None

    # Program
    prog = Program(id=10, name="6B06101 - Ақпараттық жүйелер", href="/prog/10", faculty=1)
    await prog.insert()
    assert (await Program.find_one(Program.item_id == 10)) is not None

    # Department
    dept = Department(id=5, name="Кафедра прикладной математики", href="/dept/5")
    await dept.insert()
    assert (await Department.find_one(Department.item_id == 5)) is not None

    # TeacherProfile
    profile = TeacherProfile(name="Ахметов А.А.", faculty="ФИЯ", department="Кафедра перевода")
    await profile.insert()
    assert (await TeacherProfile.find_one(TeacherProfile.name == "Ахметов А.А.")) is not None

    # UserRegistrationStat
    reg_stat = UserRegistrationStat(registeredUsers=1542)
    await reg_stat.insert()
    assert (await UserRegistrationStat.find_one(UserRegistrationStat.registered_users == 1542)) is not None

    # UserActivity
    act_stat = UserActivity(userActivity=854)
    await act_stat.insert()
    assert (await UserActivity.find_one(UserActivity.user_activity == 854)) is not None

    # UserToll
    toll = UserToll(userId=888, starPrice=5000, paidCredits=10)
    await toll.insert()
    assert (await UserToll.find_one(UserToll.user_id == 888)).paid_credits == 10

    # Log
    log_doc = Log(level="INFO", message="System booted", meta={"version": "2.0.0"})
    await log_doc.insert()
    assert (await Log.find_one(Log.level == "INFO")).message == "System booted"


@pytest.mark.asyncio
async def test_user_action_aggregation_pipeline(mock_db):
    """Verifies MongoDB Aggregation Pipeline on UserAction collection (Render memory safety)."""
    # Insert multiple actions
    for i in range(5):
        await UserAction(
            userId=100 + i,
            action="view_schedule",
            text="Group A",
        ).insert()

    # Perform aggregation on the underlying Motor collection without loading all into RAM
    collection = UserAction.get_motor_collection()
    cursor = collection.aggregate([
        {"$match": {"action": "view_schedule"}},
        {"$group": {"_id": "$action", "total": {"$sum": 1}}},
    ])
    results = await cursor.to_list(length=10)
    assert len(results) == 1
    assert results[0]["total"] == 5


@pytest.mark.asyncio
async def test_domain_id_query_expressions(mock_db):
    """Verifies that Beanie queries by domain id field work natively."""
    # 1. Group query by group_id and item_id
    group = Group(group_id=201, name="ИС-22-1")
    await group.insert()
    assert (await Group.find_one(Group.group_id == 201)) is not None
    assert (await Group.find_one(Group.item_id == 201)) is not None
    assert (await Group.find_one({"id": 201})) is not None

    # 2. Teacher query by teacher_id and item_id
    teacher = Teacher(teacher_id=301, name="Касымов К.К.")
    await teacher.insert()
    assert (await Teacher.find_one(Teacher.teacher_id == 301)) is not None
    assert (await Teacher.find_one(Teacher.item_id == 301)) is not None

    # 3. Faculty query by faculty_id and item_id
    faculty = Faculty(faculty_id=401, name="Физический факультет")
    await faculty.insert()
    assert (await Faculty.find_one(Faculty.faculty_id == 401)) is not None
    assert (await Faculty.find_one(Faculty.item_id == 401)) is not None

    # 4. Program query by program_id and item_id
    program = Program(program_id=501, name="Радиотехника")
    await program.insert()
    assert (await Program.find_one(Program.program_id == 501)) is not None
    assert (await Program.find_one(Program.item_id == 501)) is not None

    # 5. Department query by department_id and item_id
    dept = Department(department_id=601, name="Кафедра радиофизики")
    await dept.insert()
    assert (await Department.find_one(Department.department_id == 601)) is not None
    assert (await Department.find_one(Department.item_id == 601)) is not None


@pytest.mark.asyncio
async def test_mongoose_null_and_empty_edge_cases(mock_db):
    """Verifies resilience against null and empty string fields from legacy Mongoose collections."""
    # User with null boolean and empty string references
    user = User.model_validate({
        "userId": 555666,
        "isAdmin": None,
        "group": "",
        "teacher": "",
        "createdAt": None,
        "updatedAt": None,
    })
    await user.insert()
    assert user.is_admin is False
    assert user.group is None
    assert user.teacher is None
    assert user.created_at is not None

    # Group with null program, empty studentCount and age
    group = Group.model_validate({
        "id": 777,
        "name": "TEST-01",
        "program": None,
        "studentCount": "",
        "age": "",
    })
    await group.insert()
    assert group.program is None
    assert group.student_count is None
    assert group.age is None

    # Teacher with null department and null isHead
    teacher = Teacher.model_validate({
        "id": 888,
        "name": "Петров П.П.",
        "department": None,
        "isHead": None,
        "photoUrl": None,
        "departmentIdOnSite": "",
    })
    await teacher.insert()
    assert teacher.department is None
    assert teacher.is_head is False
    assert teacher.department_id_on_site is None

    # Program & Department with null href / faculty
    prog = Program.model_validate({"id": 991, "name": "Тест программа", "href": None, "faculty": None})
    await prog.insert()
    assert prog.href is None
    assert prog.faculty is None

    dept = Department.model_validate({"id": 992, "name": "Тест кафедра", "href": None})
    await dept.insert()
    assert dept.href is None

    # Schedule with null data and ScheduleDay with null subjects
    sched = Schedule.model_validate({"groupId": 993, "data": None})
    await sched.insert()
    assert sched.data == []

    sday = ScheduleDay.model_validate({"day": "Понедельник", "subjects": None})
    assert sday.subjects == []

    # TeacherSchedule with null data and TeacherScheduleDay with null groups
    tsched = TeacherSchedule.model_validate({"teacherId": 994, "data": None})
    await tsched.insert()
    assert tsched.data == []

    tsday = TeacherScheduleDay.model_validate({"day": "Вторник", "groups": None})
    assert tsday.groups == []

    # ScheduleVersion & TeacherScheduleVersion with null lists and archivedAt
    sver = ScheduleVersion.model_validate({
        "groupId": 995,
        "hash": "hash_v",
        "data": None,
        "diffDetails": None,
        "archivedAt": None,
    })
    await sver.insert()
    assert sver.data == []
    assert sver.diff_details == []
    assert sver.archived_at is not None

    tsver = TeacherScheduleVersion.model_validate({
        "teacherId": 996,
        "hash": "hash_tv",
        "data": None,
        "diffDetails": None,
        "archivedAt": None,
    })
    await tsver.insert()
    assert tsver.data == []
    assert tsver.diff_details == []
    assert tsver.archived_at is not None

    # UserAction with null isFirst and empty entityId
    action = UserAction.model_validate({
        "userId": 997,
        "action": "click",
        "text": "btn",
        "isFirst": None,
        "entityId": "",
        "createdAt": None,
    })
    await action.insert()
    assert action.is_first is False
    assert action.entity_id is None
    assert action.created_at is not None

    # UserRegistrationStat & UserActivity with nulls
    reg_stat = UserRegistrationStat.model_validate({"registeredUsers": None})
    assert reg_stat.registered_users == 0

    act_stat = UserActivity.model_validate({"userActivity": None})
    assert act_stat.user_activity == 0

    # UserToll with nulls and empty addedBy
    toll = UserToll.model_validate({
        "userId": 998,
        "enabled": None,
        "starPrice": None,
        "paidCredits": None,
        "addedBy": "",
    })
    await toll.insert()
    assert toll.enabled is True
    assert toll.star_price == 9000
    assert toll.paid_credits == 0
    assert toll.added_by is None


def test_user_action_compound_index():
    """Verifies that UserAction model specifies [('createdAt', -1), ('action', 1)] compound index."""
    from pymongo import ASCENDING, DESCENDING
    from src.database.models.user_action import UserAction

    compound_idx = next(
        (
            idx
            for idx in UserAction.Settings.indexes
            if list(getattr(idx.document.get("key"), "items", lambda: [])())
            == [("createdAt", DESCENDING), ("action", ASCENDING)]
        ),
        None,
    )
    assert compound_idx is not None, "Compound index [('createdAt', -1), ('action', 1)] not found in UserAction"


