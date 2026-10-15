"""Educational program / specialty document model."""

from typing import Any, ClassVar
from pymongo import IndexModel, ASCENDING
from pydantic import Field, field_validator
from src.database.base import BaseMongoDoc, ItemIdDescriptor


class Program(BaseMongoDoc):
    """Educational program within a faculty."""

    program_id: int = Field(alias="id")
    name: str
    href: str | None = Field(default=None)
    faculty: int | None = Field(default=None, description="Faculty ID reference")

    item_id: ClassVar[Any] = ItemIdDescriptor("program_id")

    @field_validator("faculty", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        if v == "" or v is None:
            return None
        return v

    class Settings:
        name = "programs"
        indexes = [
            IndexModel([("href", ASCENDING)], unique=True),
            IndexModel([("faculty", ASCENDING)]),
            IndexModel([("id", ASCENDING)]),
        ]
