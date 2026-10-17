"""Student schedule and schedule versioning document models."""

from datetime import datetime, timezone
from typing import Any
from pymongo import IndexModel, ASCENDING, DESCENDING
from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.database.base import BaseMongoDoc


class SubjectItem(BaseModel):
    """Single lesson/subject slot in student schedule."""

    model_config = ConfigDict(extra="ignore")

    time: str | None = Field(default=None)
    subject: str | None = Field(default=None)


class ScheduleDay(BaseModel):
    """Day schedule containing list of subjects."""

    model_config = ConfigDict(extra="ignore")

    day: str
    subjects: list[SubjectItem] = Field(default_factory=list)

    @field_validator("subjects", mode="before")
    @classmethod
    def _coerce_subjects(cls, v: Any) -> Any:
        return v if v is not None else []


class Schedule(BaseMongoDoc):
    """Permanent fallback schedule storage for a student group."""

    group_id: int = Field(alias="groupId")
    data: list[ScheduleDay] = Field(default_factory=list)
    hash: str | None = Field(default=None, description="SHA-256 hash of schedule content")
    changed_at: datetime | None = Field(default=None, alias="changedAt")
    checked_at: datetime | None = Field(default=None, alias="checkedAt")
    has_schedule: bool | None = Field(default=None, alias="hasSchedule")
    permanent_empty: bool | None = Field(default=None, alias="permanentEmpty")
    last_checked_at: datetime | None = Field(default=None, alias="lastCheckedAt")

    @field_validator("data", mode="before")
    @classmethod
    def _coerce_data(cls, v: Any) -> Any:
        return v if v is not None else []

    class Settings:
        name = "schedules"
        indexes = [
            IndexModel([("groupId", ASCENDING)], unique=True),
        ]


class ScheduleVersion(BaseMongoDoc):
    """Archived historical version of a student group schedule (TTL 60 days)."""

    group_id: int = Field(alias="groupId")
    hash: str
    data: list[ScheduleDay] = Field(default_factory=list)
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
        name = "scheduleversions"
        indexes = [
            IndexModel([("groupId", ASCENDING), ("validTo", DESCENDING)]),
            IndexModel([("archivedAt", ASCENDING)], expireAfterSeconds=60 * 24 * 60 * 60),
        ]
