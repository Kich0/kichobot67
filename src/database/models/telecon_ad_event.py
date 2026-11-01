"""Terricon Valley advertisement event document model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from pymongo import ASCENDING, DESCENDING, IndexModel
from pydantic import Field, field_validator

from src.database.base import BaseMongoDoc


class TeleconAdEvent(BaseMongoDoc):
    """Advertisement event for partner / sponsor «Terricon Valley».

    Stored permanently (without TTL) in MongoDB collection 'telecon_ad_events'
    to provide exact analytics on impressions, button clicks, link clicks,
    and user interactions.
    """

    user_id: int = Field(default=0, alias="userId")
    username: str | None = Field(default=None)
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    event_type: str = Field(
        alias="eventType",
        description="'button_click', 'link_click', 'impression'",
    )
    source: str = Field(
        default="student_schedule",
        description="'student_schedule', 'teacher_schedule', 'web_redirect', 'direct'",
    )
    target_url: str = Field(
        default="https://2gis.kz/karaganda/firm/70000001083364853",
        alias="targetUrl",
    )
    button_text: str | None = Field(
        default="👉 Подробнее",
        alias="buttonText",
    )
    ip: str | None = Field(default=None)
    user_agent: str | None = Field(default=None, alias="userAgent")
    details: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="createdAt",
    )

    @field_validator("user_id", mode="before")
    @classmethod
    def _coerce_user_id(cls, v: Any) -> int:
        if v is None:
            return 0
        if isinstance(v, int):
            return v
        try:
            return int(str(v).strip())
        except (ValueError, TypeError):
            return 0

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, v: Any) -> Any:
        return v if v is not None else datetime.now(timezone.utc)

    class Settings:
        name = "telecon_ad_events"
        indexes = [
            IndexModel([("createdAt", DESCENDING)]),
            IndexModel([("eventType", ASCENDING), ("createdAt", DESCENDING)]),
            IndexModel([("userId", ASCENDING), ("eventType", ASCENDING)]),
            IndexModel([("source", ASCENDING)]),
        ]
