"""User activity count document model."""

from typing import Any
from pydantic import Field, field_validator
from src.database.base import BaseMongoDoc


class UserActivity(BaseMongoDoc):
    """Daily snapshot of user activity events."""

    user_activity: int = Field(default=0, alias="userActivity")

    @field_validator("user_activity", mode="before")
    @classmethod
    def _coerce_user_activity(cls, v: Any) -> int:
        if v is None or v == "":
            return 0
        return int(v)

    class Settings:
        name = "user-activities"
