"""Broadcast message document model for tracking scheduled auto-deletions."""

from datetime import datetime, timezone
from beanie import Document
from pydantic import Field


class BroadcastMessage(Document):
    """Tracks sent broadcast message id and planned deletion time."""

    chat_id: int = Field(index=True)
    message_id: int = Field(index=True)
    delete_at: datetime = Field(index=True)
    is_deleted: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "broadcast_messages"
