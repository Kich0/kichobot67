"""Teacher schedule and schedule versioning document models."""

from datetime import datetime, timezone
from typing import Any
from pymongo import IndexModel, ASCENDING, DESCENDING
from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.database.base import BaseMongoDoc


class TeacherLessonItem(BaseModel):
    """Single lesson slot in teacher schedule."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    time: str | None = Field(default=None)
    group: str | None = Field(default=None)
    subject: str | None = Field(default=None)
    lesson_type: str | None = Field(default=None, alias="lessonType")


class TeacherScheduleDay(BaseModel):
    """Day schedule containing list of teacher lessons/groups."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    day: str
    groups: list[TeacherLessonItem] = Field(default_factory=list)

    @field_validator("groups", mode="before")
    @classmethod
    def _coerce_groups(cls, v: Any) -> Any:
        return v if v is not None else []


class TeacherSchedule(BaseMongoDoc):
    """Permanent fallback schedule storage for a teacher."""

    teacher_id: int = Field(alias="teacherId")
    data: list[TeacherScheduleDay] = Field(default_factory=list)
    hash: str | None = Field(default=None, description="SHA-256 hash of schedule content")
    changed_at: datetime | None = Field(default=None, alias="changedAt")
    checked_at: datetime | None = Field(default=None, alias="checkedAt")

    @field_validator("data", mode="before")
    @classmethod
    def _coerce_data(cls, v: Any) -> Any:
        return v if v is not None else []

    class Settings:
        name = "teacherschedules"
        indexes = [
            IndexModel([("teacherId", ASCENDING)], unique=True),
        ]


class TeacherScheduleVersion(BaseMongoDoc):
    """Archived historical version of a teacher schedule (TTL 60 days)."""

    teacher_id: int = Field(alias="teacherId")
    hash: str
    data: list[TeacherScheduleDay] = Field(default_factory=list)
    valid_from: datetime | None = Field(default=None, alias="validFrom")
    valid_to: datetime | None = Field(default=None, alias="validTo")
    diff_summary: str | None = Field(default=None, alias="diffSummary")
    diff_details: list[Any] = Field(default_factory=list, alias="diffDetails")
    archived_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="archivedAt",
    )

    @field_validator("data", "diff_details", mode="before")
    @classmethod
    def _coerce_lists(cls, v: Any) -> Any:
        return v if v is not None else []

    @field_validator("archived_at", mode="before")
    @classmethod
    def _coerce_archived_at(cls, v: Any) -> Any:
        return v if v is not None else datetime.now(timezone.utc)

    class Settings:
        name = "teacherscheduleversions"
        indexes = [
            IndexModel([("teacherId", ASCENDING), ("validTo", DESCENDING)]),
            IndexModel([("archivedAt", ASCENDING)], expireAfterSeconds=60 * 24 * 60 * 60),
        ]
