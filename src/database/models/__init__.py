"""Collection of all Beanie document models for Kichobot."""

from src.database.models.user import User
from src.database.models.group import Group
from src.database.models.teacher import Teacher
from src.database.models.faculty import Faculty
from src.database.models.program import Program
from src.database.models.department import Department
from src.database.models.teacher_profile import TeacherProfile
from src.database.models.schedule import Schedule, ScheduleVersion, ScheduleDay, SubjectItem
from src.database.models.teacher_schedule import (
    TeacherSchedule,
    TeacherScheduleVersion,
    TeacherScheduleDay,
    TeacherLessonItem,
)
from src.database.models.user_action import UserAction
from src.database.models.black_list import BlackList
from src.database.models.log import Log
from src.database.models.user_registration_stat import UserRegistrationStat
from src.database.models.user_activity import UserActivity
from src.database.models.user_toll import UserToll

ALL_DOCUMENT_MODELS = [
    User,
    Group,
    Teacher,
    Faculty,
    Program,
    Department,
    TeacherProfile,
    Schedule,
    ScheduleVersion,
    TeacherSchedule,
    TeacherScheduleVersion,
    UserAction,
    BlackList,
    Log,
    UserRegistrationStat,
    UserActivity,
    UserToll,
]

__all__ = [
    "User",
    "Group",
    "Teacher",
    "Faculty",
    "Program",
    "Department",
    "TeacherProfile",
    "Schedule",
    "ScheduleVersion",
    "ScheduleDay",
    "SubjectItem",
    "TeacherSchedule",
    "TeacherScheduleVersion",
    "TeacherScheduleDay",
    "TeacherLessonItem",
    "UserAction",
    "BlackList",
    "Log",
    "UserRegistrationStat",
    "UserActivity",
    "UserToll",
    "ALL_DOCUMENT_MODELS",
]
