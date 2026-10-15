"""Faculty document model."""

from typing import Any, ClassVar
from pymongo import IndexModel, ASCENDING
from pydantic import Field
from src.database.base import BaseMongoDoc, ItemIdDescriptor


class Faculty(BaseMongoDoc):
    """University faculty / institute."""

    faculty_id: int = Field(alias="id")
    name: str

    item_id: ClassVar[Any] = ItemIdDescriptor("faculty_id")

    class Settings:
        name = "faculties"
        indexes = [
            IndexModel([("id", ASCENDING)], unique=True),
            IndexModel([("name", ASCENDING)], unique=True),
        ]
