"""Service for managing broadcast deliveries and scheduled auto-deletion of announcements.

Handles:
- Duration string parsing (e.g. '4h', '30m', '0', 'perm')
- Auto-delete background workers
- Database persistence for auto-deletions surviving server restarts
- Scheduled sweeper to delete expired broadcasts
"""

import asyncio
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from src.core.logging import get_logger

logger = get_logger("broadcast_service")


def parse_broadcast_duration(raw: str | None) -> tuple[int | None, bool]:
    """Parses duration string like '4h', '2h', '30m', '0', 'perm'.

    Returns (seconds, is_explicit_duration):
    - (seconds, True) if an explicit duration token was recognized.
    - (None, False) if the token is not a recognized duration.
    """
    if not raw:
        return None, False

    clean = raw.lower().strip()
    if clean in ("0", "perm", "permanent", "forever", "inf", "навсегда", "нет"):
        return 0, True

    match = re.match(r"^(\d+(?:\.\d+)?)\s*(h|ч|m|мин|s|сек|d|д)?$", clean)
    if not match:
        return None, False

    val = float(match.group(1))
    unit = match.group(2)
    # Default to hours if no unit given and value is reasonable for hours
    if not unit:
        unit = "h" if val <= 48 else "s"

    if unit in ("h", "ч"):
        return int(val * 3600), True
    elif unit in ("m", "мин"):
        return int(val * 60), True
    elif unit in ("d", "д"):
        return int(val * 86400), True
    elif unit in ("s", "сек"):
        return int(val), True

    return None, False


def format_broadcast_duration(seconds: int) -> str:
    """Formats duration in seconds to a human-readable Russian string."""
    if seconds <= 0:
        return "без автоудаления (навсегда)"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} мин.")
    if secs > 0 and hours == 0:
        parts.append(f"{secs} сек.")

    return " ".join(parts) if parts else f"{seconds} сек."


class BroadcastService:
    """Manages scheduled deletions of broadcast messages."""

    DEFAULT_BROADCAST_DELETE_SECONDS: int = 4 * 3600  # Default 4 hours

    async def record_delivery_batch(
        self,
        delivered: list[tuple[int, int]],
        delete_after_seconds: int,
    ) -> None:
        """Persists delivered messages to MongoDB for restart resilience."""
        if delete_after_seconds <= 0 or not delivered:
            return

        try:
            from src.database.models.broadcast_message import BroadcastMessage

            now = datetime.now(timezone.utc)
            delete_at = now + timedelta(seconds=delete_after_seconds)
            docs = [
                BroadcastMessage(
                    chat_id=cid,
                    message_id=mid,
                    delete_at=delete_at,
                    is_deleted=False,
                    created_at=now,
                )
                for cid, mid in delivered
            ]

            chunk_size = 500
            for i in range(0, len(docs), chunk_size):
                await BroadcastMessage.insert_many(docs[i : i + chunk_size])

            logger.info(
                f"Persisted {len(delivered)} broadcast messages to MongoDB with delete_at={delete_at}."
            )
        except Exception as e:
            logger.error(f"Failed to persist broadcast messages to MongoDB: {e}")

    async def run_auto_delete_worker(
        self,
        bot: Bot,
        delivered: list[tuple[int, int]],
        delay_seconds: int,
        admin_chat_id: int | None = None,
    ) -> None:
        """Sleeps for delay_seconds then deletes sent messages and notifies admin."""
        if delay_seconds <= 0 or not delivered:
            return

        logger.info(
            f"Auto-delete worker sleeping for {delay_seconds}s before deleting {len(delivered)} messages..."
        )
        await asyncio.sleep(delay_seconds)

        logger.info(f"Starting auto-delete execution for {len(delivered)} messages...")
        deleted_count = 0
        message_ids: list[int] = []

        for chat_id, message_id in delivered:
            message_ids.append(message_id)
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
                deleted_count += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)  # 50ms pacing

        # Mark in MongoDB
        try:
            from src.database.models.broadcast_message import BroadcastMessage

            if message_ids:
                await BroadcastMessage.find(
                    {"message_id": {"$in": message_ids}}
                ).update_many({"$set": {"is_deleted": True}})
        except Exception as e:
            logger.debug(f"Failed to update is_deleted in DB: {e}")

        logger.info(
            f"Auto-delete finished: removed {deleted_count}/{len(delivered)} messages."
        )

        if admin_chat_id:
            try:
                await bot.send_message(
                    admin_chat_id,
                    f"🧹 <b>Автоудаление оповещения завершено:</b>\n"
                    f"• Удалено сообщений: {deleted_count} из {len(delivered)}",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.debug(f"Failed to send auto-delete report to admin: {e}")

    async def cleanup_expired_broadcasts(self, bot: Bot) -> int:
        """Sweeps MongoDB for expired broadcasts and deletes them in Telegram."""
        try:
            from src.database.models.broadcast_message import BroadcastMessage

            now = datetime.now(timezone.utc)
            expired = (
                await BroadcastMessage.find(
                    {
                        "delete_at": {"$lte": now},
                        "is_deleted": False,
                    }
                )
                .limit(1000)
                .to_list()
            )

            if not expired:
                return 0

            logger.info(f"Sweeping {len(expired)} expired broadcast messages...")
            deleted_count = 0
            processed_ids: list[Any] = []

            for doc in expired:
                try:
                    await bot.delete_message(
                        chat_id=doc.chat_id,
                        message_id=doc.message_id,
                    )
                    deleted_count += 1
                except TelegramRetryAfter as e:
                    await asyncio.sleep(float(e.retry_after))
                    try:
                        await bot.delete_message(
                            chat_id=doc.chat_id,
                            message_id=doc.message_id,
                        )
                        deleted_count += 1
                    except Exception:
                        pass
                except Exception:
                    pass
                processed_ids.append(doc.id)
                await asyncio.sleep(0.05)

            if processed_ids:
                await BroadcastMessage.find(
                    {"_id": {"$in": processed_ids}}
                ).update_many({"$set": {"is_deleted": True}})

            logger.info(f"Sweep complete: successfully deleted {deleted_count} expired messages.")
            return deleted_count
        except Exception as e:
            logger.debug(f"Error during expired broadcasts sweep: {e}")
            return 0


broadcast_service = BroadcastService()

__all__ = [
    "BroadcastService",
    "broadcast_service",
    "parse_broadcast_duration",
    "format_broadcast_duration",
]
