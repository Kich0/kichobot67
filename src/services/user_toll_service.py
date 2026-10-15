"""User Toll / Paid Button Service for Telegram Stars monetization.

Provides:
- In-memory cache of tolled users for O(1) button click checks.
- Integration with Beanie UserToll model for persistence.
- Credit management for paid button clicks.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.logging import get_logger

logger = get_logger("user_toll_service")


@dataclass
class TollUserRecord:
    user_id: int
    star_price: int = 9000
    paid_credits: int = 0
    username: str | None = None
    added_by: int | None = None
    created_at: datetime | None = None


class UserTollService:
    """Manages paid button restrictions and star prices per user."""

    def __init__(self) -> None:
        self._tolled_users: dict[int, TollUserRecord] = {}
        self._loaded_from_db = False

    async def ensure_loaded(self) -> None:
        """Loads enabled user tolls from MongoDB into in-memory cache."""
        if self._loaded_from_db:
            return
        try:
            from src.database.models.user_toll import UserToll

            records = await UserToll.find({"enabled": True}).to_list()
            for r in records:
                self._tolled_users[r.user_id] = TollUserRecord(
                    user_id=r.user_id,
                    star_price=r.star_price or 9000,
                    paid_credits=r.paid_credits or 0,
                    username=r.username,
                    added_by=r.added_by,
                    created_at=r.created_at,
                )
            self._loaded_from_db = True
        except Exception as e:
            logger.debug(f"UserTollService initial load skipped/failed: {e}")

    def is_user_tolled(self, user_id: int) -> bool:
        """Checks if a user is currently subject to paid button mode."""
        if not user_id:
            return False
        return user_id in self._tolled_users

    def has_paid_credit(self, user_id: int) -> bool:
        """Checks if user has any prepaid button click credits."""
        rec = self._tolled_users.get(user_id)
        return bool(rec and rec.paid_credits > 0)

    def consume_paid_credit(self, user_id: int) -> bool:
        """Consumes 1 paid click credit and updates MongoDB in the background."""
        rec = self._tolled_users.get(user_id)
        if rec and rec.paid_credits > 0:
            rec.paid_credits -= 1

            async def _persist_decrement() -> None:
                try:
                    from src.database.models.user_toll import UserToll

                    doc = await UserToll.find_one({"userId": user_id})
                    if doc:
                        doc.paid_credits = rec.paid_credits
                        await doc.save()
                except Exception as e:
                    logger.debug(f"Error decrementing paid credits in DB: {e}")

            asyncio.create_task(_persist_decrement())
            return True
        return False

    async def add_paid_credit(self, user_id: int, count: int = 1) -> int:
        """Grants paid button click credits upon successful Stars payment."""
        rec = self._tolled_users.get(user_id)
        if rec:
            rec.paid_credits += count
        else:
            rec = TollUserRecord(
                user_id=user_id,
                star_price=9000,
                paid_credits=count,
                created_at=datetime.now(timezone.utc),
            )
            self._tolled_users[user_id] = rec

        try:
            from src.database.models.user_toll import UserToll

            await UserToll.find_one({"userId": user_id}).upsert(
                {
                    "$set": {
                        "userId": user_id,
                        "enabled": True,
                        "paidCredits": rec.paid_credits,
                        "starPrice": rec.star_price,
                    }
                },
                on_insert=UserToll(
                    userId=user_id,
                    enabled=True,
                    paidCredits=rec.paid_credits,
                    starPrice=rec.star_price,
                ),
            )
        except Exception as e:
            logger.debug(f"Error adding paid credits to DB: {e}")

        return rec.paid_credits

    def get_toll_price(self, user_id: int) -> int:
        """Returns required star price per button for this user (defaults to 9000)."""
        rec = self._tolled_users.get(user_id)
        return rec.star_price if rec else 9000

    def get_toll_info(self, user_id: int) -> TollUserRecord | None:
        """Returns toll metadata for user."""
        return self._tolled_users.get(user_id)

    async def enable_toll(
        self,
        user_id: int,
        star_price: int = 9000,
        username: str | None = None,
        added_by: int | None = None,
    ) -> TollUserRecord:
        """Enables paid buttons mode for a target user."""
        clean_user = username.lstrip("@") if username else None
        now = datetime.now(timezone.utc)
        existing = self._tolled_users.get(user_id)

        rec = TollUserRecord(
            user_id=user_id,
            star_price=int(star_price),
            paid_credits=existing.paid_credits if existing else 0,
            username=clean_user or (existing.username if existing else None),
            added_by=added_by,
            created_at=existing.created_at if existing and existing.created_at else now,
        )
        self._tolled_users[user_id] = rec

        try:
            from src.database.models.user_toll import UserToll

            await UserToll.find_one({"userId": user_id}).upsert(
                {
                    "$set": {
                        "userId": user_id,
                        "enabled": True,
                        "starPrice": rec.star_price,
                        "username": rec.username,
                        "paidCredits": rec.paid_credits,
                        "addedBy": added_by,
                    }
                },
                on_insert=UserToll(
                    userId=user_id,
                    enabled=True,
                    starPrice=rec.star_price,
                    username=rec.username,
                    paidCredits=rec.paid_credits,
                    addedBy=added_by,
                ),
            )
        except Exception as e:
            logger.debug(f"Error enabling toll in DB: {e}")

        return rec

    async def disable_toll(self, user_id: int) -> bool:
        """Disables paid buttons mode for a user."""
        if user_id in self._tolled_users:
            del self._tolled_users[user_id]

        try:
            from src.database.models.user_toll import UserToll

            doc = await UserToll.find_one({"userId": user_id})
            if doc:
                doc.enabled = False
                await doc.save()
            return True
        except Exception as e:
            logger.debug(f"Error disabling toll in DB: {e}")
            return False

    def get_all_tolled(self) -> list[TollUserRecord]:
        """Returns list of all active tolled user records."""
        return list(self._tolled_users.values())

    def clear_cache(self) -> None:
        """Resets in-memory toll registry (useful for unit tests)."""
        self._tolled_users.clear()
        self._loaded_from_db = False


user_toll_service = UserTollService()

__all__ = ["TollUserRecord", "UserTollService", "user_toll_service"]
