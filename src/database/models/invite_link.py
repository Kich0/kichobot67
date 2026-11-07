"""Invite link persistence document model."""

from __future__ import annotations

from typing import Any
from pymongo import ASCENDING, DESCENDING, IndexModel
from pydantic import Field

from src.database.base import BaseMongoDoc


class InviteLink(BaseMongoDoc):
    """Stores one-time Telegram invite links created by @Karu_proxy_bot."""

    invite_link: str = Field(alias="inviteLink")
    chat_id: int = Field(alias="chatId")
    status: str = Field(default="active")  # 'active', 'used', 'left', 'banned', 'expired'
    member_limit: int = Field(default=1, alias="memberLimit")
    expire_date: str | None = Field(default=None, alias="expireDate")
    used_at: str | None = Field(default=None, alias="usedAt")
    used_by: dict[str, Any] | None = Field(default=None, alias="usedBy")
    left_at: str | None = Field(default=None, alias="leftAt")
    banned_at: str | None = Field(default=None, alias="bannedAt")

    class Settings:
        name = "invite_links"
        indexes = [
            IndexModel([("inviteLink", ASCENDING)], unique=True),
            IndexModel([("chatId", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("usedBy.telegram_id", ASCENDING)]),
            IndexModel([("createdAt", DESCENDING)]),
        ]


__all__ = ["InviteLink"]
