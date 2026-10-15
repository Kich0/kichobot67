"""User document model."""

from typing import Any
from pymongo import IndexModel, ASCENDING, DESCENDING
from pydantic import Field, field_validator
from src.database.base import BaseMongoDoc


class User(BaseMongoDoc):
    """User profile and preferences."""

    user_id: int = Field(alias="userId")
    user_type: str | None = Field(default=None, alias="userType")
    user_title: str | None = Field(default=None, alias="userTitle")
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    username: str | None = Field(default=None)
    group: int | None = Field(default=None)
    teacher: int | None = Field(default=None)
    schedule_type: str | None = Field(default=None, alias="scheduleType")
    is_admin: bool = Field(default=False, alias="isAdmin")
    language: str | None = Field(default="ru")

    @field_validator("is_admin", mode="before")
    @classmethod
    def _coerce_is_admin(cls, v: Any) -> bool:
        if v is None:
            return False
        return bool(v)

    @field_validator("group", "teacher", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        if v == "" or v is None:
            return None
        return v

    class Settings:
        name = "users"
        indexes = [
            IndexModel([("userId", ASCENDING)], unique=True),
            IndexModel([("username", ASCENDING)]),
            IndexModel([("group", ASCENDING)]),
            IndexModel([("teacher", ASCENDING)]),
            IndexModel([("createdAt", DESCENDING)]),
        ]
