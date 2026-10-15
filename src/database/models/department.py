"""Department document model."""

from typing import Any, ClassVar
from pymongo import IndexModel, ASCENDING
from pydantic import Field
from src.database.base import BaseMongoDoc, ItemIdDescriptor


class Department(BaseMongoDoc):
    """Academic department."""

    department_id: int = Field(alias="id")
    name: str
    href: str | None = Field(default=None)

    item_id: ClassVar[Any] = ItemIdDescriptor("department_id")

    class Settings:
        name = "departments"
        indexes = [
            IndexModel([("id", ASCENDING)], unique=True),
            IndexModel([("href", ASCENDING)], unique=True),
            IndexModel([("name", ASCENDING)]),
        ]
