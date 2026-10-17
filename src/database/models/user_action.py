"""User activity action log document model."""

from typing import Any
from datetime import datetime, timezone
from pymongo import IndexModel, ASCENDING, DESCENDING
from pydantic import Field, field_validator
from src.database.base import BaseMongoDoc


class UserAction(BaseMongoDoc):
    """User audit log entry (button clicks, commands, searches).

    Kept for 45 days via MongoDB TTL index on createdAt.
    """

    user_id: int = Field(alias="userId")
    username: str | None = Field(default=None)
    action: str
    text: str
    is_first: bool = Field(default=False, alias="isFirst")
    entity_id: int | None = Field(default=None, alias="entityId")
    entity_name: str | None = Field(default=None, alias="entityName")
    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="createdAt",
    )

    @field_validator("is_first", mode="before")
    @classmethod
    def _coerce_is_first(cls, v: Any) -> bool:
        if v is None:
            return False
        return bool(v)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, v: Any) -> Any:
        return v if v is not None else datetime.now(timezone.utc)

    @field_validator("entity_id", mode="before")
    @classmethod
    def _coerce_entity_id(cls, v: Any) -> int | None:
        if v == "" or v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return None

    class Settings:
        name = "useractions"
        indexes = [
            IndexModel([("userId", ASCENDING), ("createdAt", DESCENDING)]),
            IndexModel([("createdAt", ASCENDING)], expireAfterSeconds=45 * 24 * 60 * 60),
            IndexModel([("createdAt", DESCENDING), ("action", ASCENDING)]),
            IndexModel([("username", ASCENDING)]),
            IndexModel([("isFirst", ASCENDING)]),
        ]
