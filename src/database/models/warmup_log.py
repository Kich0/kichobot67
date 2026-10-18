"""Schedule cache warmup audit log document model."""

from datetime import datetime, timezone
from typing import Any
from pydantic import Field, field_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

from src.database.base import BaseMongoDoc


class WarmupLog(BaseMongoDoc):
    """Audit log entry for background schedule cache warmup.

    Tracks whether entity schedule was warmed from API, Cache, DB fallback, or error.
    Kept for 10 days via MongoDB TTL index on createdAt.
    """

    name: str
    target_type: str = Field(alias="type")
    badge: str = Field(default="🟢")
    source: str = Field(alias="src")
    duration_ms: int = Field(default=0, alias="ms")
    status: bool = Field(default=True, alias="ok")
    error_msg: str | None = Field(default=None, alias="err")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="createdAt",
    )

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, v: Any) -> Any:
        return v if v is not None else datetime.now(timezone.utc)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v: Any) -> bool:
        if v is None:
            return True
        return bool(v)

    class Settings:
        name = "warmuplogs"
        indexes = [
            IndexModel([("createdAt", ASCENDING)], expireAfterSeconds=10 * 24 * 60 * 60),
            IndexModel([("name", ASCENDING)]),
            IndexModel([("type", ASCENDING), ("createdAt", DESCENDING)]),
        ]
