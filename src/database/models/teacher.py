"""Teacher document model."""

from typing import Any, ClassVar
from pymongo import IndexModel, ASCENDING
from pydantic import Field, field_validator
from src.database.base import BaseMongoDoc, ItemIdDescriptor


class Teacher(BaseMongoDoc):
    """University teacher / faculty member."""

    teacher_id: int = Field(alias="id")
    name: str
    href: str = Field(default="")
    department: int | None = Field(default=None, description="Department ID reference")
    full_name: str | None = Field(default=None, alias="fullName")
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    patronymic: str | None = Field(default=None)
    job_title: str | None = Field(default=None, alias="jobTitle")
    photo_url: str | None = Field(default=None, alias="photoUrl")
    is_head: bool = Field(default=False, alias="isHead")
    department_name: str | None = Field(default=None, alias="departmentName")
    department_id_on_site: int | None = Field(default=None, alias="departmentIdOnSite")

    item_id: ClassVar[Any] = ItemIdDescriptor("teacher_id")

    @field_validator("is_head", mode="before")
    @classmethod
    def _coerce_is_head(cls, v: Any) -> bool:
        if v is None:
            return False
        return bool(v)

    @field_validator("department", "department_id_on_site", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        if v == "" or v is None:
            return None
        return v

    class Settings:
        name = "teachers"
        indexes = [
            IndexModel([("id", ASCENDING)], unique=True),
            IndexModel([("department", ASCENDING)]),
            IndexModel([("name", ASCENDING)]),
            IndexModel([("fullName", ASCENDING)]),
        ]
