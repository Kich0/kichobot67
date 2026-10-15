"""Student group document model."""

from typing import Any, ClassVar
from pymongo import IndexModel, ASCENDING
from pydantic import Field, field_validator
from src.database.base import BaseMongoDoc, ItemIdDescriptor


class Group(BaseMongoDoc):
    """Academic student group."""

    group_id: int = Field(alias="id")
    name: str
    language: str | None = Field(default=None)
    href: str | None = Field(default=None)
    age: int | None = Field(default=None)
    student_count: int | None = Field(default=None, alias="studentCount")
    program: int | None = Field(default=None, description="Program ID reference")

    item_id: ClassVar[Any] = ItemIdDescriptor("group_id")

    @field_validator("age", "student_count", "program", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        if v == "" or v is None:
            return None
        return v

    class Settings:
        name = "groups"
        indexes = [
            IndexModel([("id", ASCENDING)], unique=True),
            IndexModel([("program", ASCENDING)]),
            IndexModel([("name", ASCENDING)]),
        ]
