"""Invite lifecycle event document model (join, leave, ban)."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, IndexModel
from pydantic import Field

from src.database.base import BaseMongoDoc


class InviteEvent(BaseMongoDoc):
    """Audit log of member lifecycle events in target Telegram chats."""

    event: str = Field(description="'joined', 'left', 'banned'")
    status: str = Field(description="'used', 'left', 'banned'")
    telegram_id: int = Field(alias="telegramId")
    username: str | None = Field(default=None)
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    old_username: str | None = Field(default=None, alias="oldUsername")
    avatar_url: str | None = Field(default=None, alias="avatarUrl")
    avatar_file_id: str | None = Field(default=None, alias="avatarFileId")
    chat_id: int = Field(alias="chatId")
    invite_link: str | None = Field(default=None, alias="inviteLink")

    class Settings:
        name = "invite_events"
        indexes = [
            IndexModel([("createdAt", DESCENDING)]),
            IndexModel([("telegramId", ASCENDING), ("createdAt", DESCENDING)]),
            IndexModel([("event", ASCENDING)]),
            IndexModel([("createdAt", ASCENDING)], expireAfterSeconds=60 * 86400),
        ]


__all__ = ["InviteEvent"]
