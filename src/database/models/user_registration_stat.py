"""User registration daily statistics document model."""

from typing import Any
from pydantic import Field, field_validator
from src.database.base import BaseMongoDoc


class UserRegistrationStat(BaseMongoDoc):
    """Daily snapshot of total registered users."""

    registered_users: int = Field(default=0, alias="registeredUsers")

    @field_validator("registered_users", mode="before")
    @classmethod
    def _coerce_registered_users(cls, v: Any) -> int:
        if v is None or v == "":
            return 0
        return int(v)

    class Settings:
        name = "userregistrationstats"
