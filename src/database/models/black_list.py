"""Blacklist document model."""

from datetime import datetime
from typing import Literal
from pymongo import IndexModel, ASCENDING
from pydantic import Field
from src.database.base import BaseMongoDoc


class BlackList(BaseMongoDoc):
    """User ban or mute record."""

    user_id: int = Field(alias="userId")
    type: Literal["ban", "mute"] = Field(default="ban")
    until: datetime | None = Field(default=None)
    reason: str | None = Field(default=None)
    username: str | None = Field(default=None)
    banned_by: int | None = Field(default=None, alias="bannedBy")

    class Settings:
        name = "black-lists"
        indexes = [
            IndexModel([("userId", ASCENDING)], unique=True),
        ]
