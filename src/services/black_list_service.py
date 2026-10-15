"""In-memory BlackList Service for instant O(1) ban and mute checks.

Matches Node.js blackListService architecture:
- Loads active restrictions into RAM on startup.
- Performs O(1) checks in MessageGateMiddleware without MongoDB network roundtrips.
- Automatically cleans up expired temporary restrictions.
- Synchronizes changes to MongoDB Beanie model when admin issues /ban, /mute, /unban.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any

from src.core.logging import get_logger

logger = get_logger("black_list_service")


@dataclass
class RestrictionRecord:
    user_id: int
    type: str  # "ban" or "mute"
    until: datetime | None = None
    reason: str | None = None
    username: str | None = None
    banned_by: int | None = None
    created_at: datetime | None = None


class BlackListService:
    """Provides high-performance in-memory ban and mute checks with DB synchronization."""

    def __init__(self) -> None:
        self._restrictions: dict[int, RestrictionRecord] = {}
        self._loaded_from_db: bool = False
        self._cleanup_lock = asyncio.Lock()

    def clear_cache(self) -> None:
        """Clears in-memory restrictions (for testing)."""
        self._restrictions.clear()
        self._loaded_from_db = False

    async def ensure_loaded(self) -> None:
        """Loads all active blacklist entries from MongoDB into memory on startup."""
        if self._loaded_from_db:
            return

        try:
            from src.database.models.black_list import BlackList

            now_dt = datetime.now(timezone.utc)
            records = await BlackList.find().to_list()
            for r in records:
                if r.until and r.until < now_dt:
                    # Expired restriction: asynchronously clean up from DB
                    asyncio.create_task(self._safe_delete_db(r.user_id))
                    continue

                self._restrictions[r.user_id] = RestrictionRecord(
                    user_id=r.user_id,
                    type=r.type or "ban",
                    until=r.until,
                    reason=r.reason,
                    username=r.username,
                    banned_by=r.banned_by,
                    created_at=getattr(r, "created_at", None),
                )

            self._loaded_from_db = True
            logger.info(f"Loaded {len(self._restrictions)} active blacklist restrictions into RAM")
        except Exception as e:
            logger.debug(f"BlackListService initial load skipped or failed: {e}")

    async def _safe_delete_db(self, user_id: int) -> None:
        """Safely removes an expired or unbanned record from MongoDB."""
        try:
            from src.database.models.black_list import BlackList

            await BlackList.find({"userId": user_id}).delete()
        except Exception as e:
            logger.debug(f"Failed to delete expired blacklist entry {user_id}: {e}")

    def get_restriction(self, user_id: int) -> RestrictionRecord | None:
        """Retrieves active restriction for a user, automatically purging if expired."""
        if not user_id:
            return None

        rec = self._restrictions.get(user_id)
        if not rec:
            return None

        if rec.until:
            now_dt = datetime.now(timezone.utc)
            if now_dt > rec.until:
                del self._restrictions[user_id]
                asyncio.create_task(self._safe_delete_db(user_id))
                return None

        return rec

    def is_banned(self, user_id: int) -> bool:
        """Instant O(1) in-memory check if user is banned."""
        rec = self.get_restriction(user_id)
        return bool(rec and rec.type == "ban")

    def is_muted(self, user_id: int) -> bool:
        """Instant O(1) in-memory check if user is muted."""
        rec = self.get_restriction(user_id)
        return bool(rec and rec.type == "mute")

    async def add_ban(
        self,
        user_id: int,
        until: datetime | None = None,
        reason: str | None = None,
        username: str | None = None,
        banned_by: int | None = None,
    ) -> RestrictionRecord:
        """Adds or updates a ban in RAM and MongoDB."""
        rec = RestrictionRecord(
            user_id=user_id,
            type="ban",
            until=until,
            reason=reason.strip() if reason else None,
            username=username.lstrip("@") if username else None,
            banned_by=banned_by,
            created_at=datetime.now(timezone.utc),
        )
        self._restrictions[user_id] = rec

        try:
            from src.database.models.black_list import BlackList

            await BlackList.find({"userId": user_id}).delete()
            doc = BlackList(
                userId=user_id,
                type="ban",
                until=until,
                reason=rec.reason,
                username=rec.username,
                bannedBy=banned_by,
            )
            await doc.insert()
        except Exception as e:
            logger.error(f"Error persisting ban for {user_id}: {e}")

        return rec

    async def add_mute(
        self,
        user_id: int,
        until: datetime | None = None,
        reason: str | None = None,
        username: str | None = None,
        banned_by: int | None = None,
    ) -> RestrictionRecord:
        """Adds or updates a mute in RAM and MongoDB."""
        rec = RestrictionRecord(
            user_id=user_id,
            type="mute",
            until=until,
            reason=reason.strip() if reason else None,
            username=username.lstrip("@") if username else None,
            banned_by=banned_by,
            created_at=datetime.now(timezone.utc),
        )
        self._restrictions[user_id] = rec

        try:
            from src.database.models.black_list import BlackList

            await BlackList.find({"userId": user_id}).delete()
            doc = BlackList(
                userId=user_id,
                type="mute",
                until=until,
                reason=rec.reason,
                username=rec.username,
                bannedBy=banned_by,
            )
            await doc.insert()
        except Exception as e:
            logger.error(f"Error persisting mute for {user_id}: {e}")

        return rec

    async def remove(self, user_id: int) -> bool:
        """Removes a restriction from RAM and MongoDB."""
        had_entry = user_id in self._restrictions
        self._restrictions.pop(user_id, None)

        try:
            from src.database.models.black_list import BlackList

            await BlackList.find({"userId": user_id}).delete()
        except Exception as e:
            logger.error(f"Error removing restriction for {user_id}: {e}")

        return had_entry

    def get_all(self) -> list[RestrictionRecord]:
        """Returns all currently active restrictions."""
        return list(self._restrictions.values())


# Global singleton instance
black_list_service = BlackListService()

__all__ = [
    "BlackListService",
    "RestrictionRecord",
    "black_list_service",
]
