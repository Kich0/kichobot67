"""User Action Logging Middleware for aiogram 3.

Records user interactions (commands, text queries, callback queries) into the
MongoDB UserAction collection asynchronously (fire-and-forget background task)
with first interaction tracking and text length capping.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.core.logging import get_logger

logger = get_logger("user_action_logger")


def format_capped_text(raw_text: Any) -> str:
    """Formats and caps log text to max 50 words and 250 characters."""
    if not raw_text:
        return ""
    text = str(raw_text).strip()
    if not text:
        return ""
    words = re.split(r"\s+", text)
    if len(words) > 50:
        text = " ".join(words[:50])
    if len(text) > 250:
        text = text[:250]
    return text


def get_media_category(msg: Message) -> tuple[str, str]:
    """Identifies media type and returns action name and descriptive label."""
    if msg.photo:
        return "media_photo", "[Медиа: фото]"
    if msg.video:
        return "media_video", "[Медиа: видео]"
    if msg.animation:
        return "media_gif", "[Медиа: GIF / анимация]"
    if msg.sticker:
        emoji = f" {msg.sticker.emoji}" if msg.sticker.emoji else ""
        return "media_sticker", f"[Медиа: стикер{emoji}]"
    if msg.voice:
        return "media_voice", "[Медиа: голосовое сообщение]"
    if msg.video_note:
        return "media_video_note", "[Медиа: видеосообщение / кружочек]"
    if msg.audio:
        return "media_audio", "[Медиа: аудиофайл]"
    if msg.document:
        fname = f" {msg.document.file_name}" if msg.document.file_name else ""
        return "media_document", f"[Медиа: документ{fname}]"
    return "media_other", "[Медиа: файл]"


class UserActionLoggingMiddleware(BaseMiddleware):
    """Asynchronously logs user interactions into the UserAction database."""

    def __init__(self) -> None:
        super().__init__()
        self._known_active_users: set[int] = set()
        self._known_button_users: set[int] = set()
        self._queue: list[dict[str, Any]] = []
        self._batch_threshold = 25
        self._max_queue_size = 2000
        self._is_flushing = False
        self._flush_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None

    def clear_cache(self) -> None:
        """Clears in-memory active user sets and queue."""
        self._known_active_users.clear()
        self._known_button_users.clear()
        self._queue.clear()
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None

    async def flush(self) -> None:
        """Flushes enqueued action records to MongoDB."""
        async with self._flush_lock:
            if not self._queue:
                return
            items_to_save = self._queue[:]
            self._queue.clear()

        try:
            from src.database.models.user_action import UserAction

            docs = [
                UserAction(
                    userId=r["user_id"],
                    username=r["username"],
                    action=r["action"],
                    text=r["text"],
                    isFirst=r["is_first"],
                    entityId=r.get("entity_id"),
                    entityName=r.get("entity_name"),
                    createdAt=r.get("created_at") or datetime.now(timezone.utc),
                )
                for r in items_to_save
            ]
            await UserAction.insert_many(docs)
        except Exception as e:
            logger.debug(f"User action batch flush error: {e}")
            # Fallback: try individual inserts
            for r in items_to_save:
                try:
                    from src.database.models.user_action import UserAction

                    action_doc = UserAction(
                        userId=r["user_id"],
                        username=r["username"],
                        action=r["action"],
                        text=r["text"],
                        isFirst=r["is_first"],
                        entityId=r.get("entity_id"),
                        entityName=r.get("entity_name"),
                        createdAt=r.get("created_at") or datetime.now(timezone.utc),
                    )
                    await action_doc.insert()
                except Exception:
                    pass

    async def _save_record(self, record: dict[str, Any]) -> None:
        """Enqueues record and triggers debounced background flush."""
        self._queue.append(record)
        if len(self._queue) > self._max_queue_size:
            # Drop oldest records to prevent unbounded RAM growth
            self._queue.pop(0)

        if len(self._queue) >= self._batch_threshold:
            asyncio.create_task(self.flush())
        elif self._flush_task is None or self._flush_task.done():
            async def _debounced() -> None:
                await asyncio.sleep(0.02)
                await self.flush()

            self._flush_task = asyncio.create_task(_debounced())

    async def _check_has_prior_action(self, user_id: int) -> bool:
        """Checks whether user has performed any prior interaction in queue or DB."""
        if any(r["user_id"] == user_id for r in self._queue):
            return True
        try:
            from src.database.models.user_action import UserAction

            prior = await UserAction.find_one({"userId": user_id})
            return prior is not None
        except Exception:
            return False

    async def _check_has_prior_button(self, user_id: int) -> bool:
        """Checks whether user has clicked any prior button in queue or DB."""
        is_btn = lambda a: a not in ("command", "chat_input", "first_message") and not a.startswith("media_")
        if any(r["user_id"] == user_id and is_btn(r["action"]) for r in self._queue):
            return True
        try:
            from beanie.operators import NotIn
            from src.database.models.user_action import UserAction

            prior_btn = await UserAction.find_one(
                UserAction.user_id == user_id,
                NotIn(
                    UserAction.action,
                    [
                        "command",
                        "chat_input",
                        "first_message",
                        "media_photo",
                        "media_sticker",
                        "media_video",
                        "media_voice",
                        "media_document",
                    ],
                ),
            )
            return prior_btn is not None
        except Exception:
            return False

    async def log_action(
        self,
        user_id: int,
        username: str | None,
        action: str,
        text: str,
        entity_id: int | None = None,
        entity_name: str | None = None,
    ) -> None:
        """Logs an action with first-time interaction checking."""
        if not user_id:
            return

        clean_username = username.lstrip("@") if username else None
        safe_action = (action or "action").strip()
        raw_text = format_capped_text(text) or safe_action

        is_button_action = safe_action not in ("command", "chat_input", "first_message") and not safe_action.startswith("media_")

        is_first_action = False
        is_first_button = False

        # First action check
        if user_id not in self._known_active_users:
            has_prior = await self._check_has_prior_action(user_id)
            if not has_prior:
                is_first_action = True
            self._known_active_users.add(user_id)

        # First button click check
        if is_button_action and user_id not in self._known_button_users:
            has_prior_button = await self._check_has_prior_button(user_id)
            if not has_prior_button:
                is_first_button = True
            self._known_button_users.add(user_id)

        is_first = False
        safe_text = raw_text

        if is_first_action:
            is_first = True
            if is_button_action:
                safe_text = f"🆕 [Первое действие пользователя (кнопка)] {raw_text}"
            else:
                safe_text = f"🆕 [Первое сообщение пользователя] {raw_text}"
        elif is_first_button:
            is_first = True
            safe_text = f"🆕 [Первое нажатие кнопки] {raw_text}"

        record = {
            "user_id": user_id,
            "username": clean_username,
            "action": safe_action,
            "text": safe_text,
            "is_first": is_first,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "created_at": datetime.now(timezone.utc),
        }

        # Save record through micro-batch queue
        await self._save_record(record)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if not user or not user.id:
            return await handler(event, data)

        user_id = user.id
        username = user.username

        if isinstance(event, Message):
            is_private = (event.chat.type == "private") if (hasattr(event, "chat") and event.chat) else True
            is_cmd = bool(
                event.text
                and (
                    event.text.startswith("/")
                    or any(event.text.startswith(p) for p in ("🗒", "🗓", "Г ", "Т ", "П ", "О "))
                )
            )

            # In group chats/conversations, only log explicit bot commands and menu clicks.
            # Casual chatter between group members is strictly ignored.
            if not is_private and not is_cmd:
                return await handler(event, data)

            if event.text:
                if event.text.startswith("/"):
                    action = "command"
                elif any(event.text.startswith(p) for p in ("🗒", "🗓", "Г ", "Т ", "П ", "О ")):
                    action = "menu_button"
                else:
                    action = "chat_input"
                text = event.text
            else:
                action, text = get_media_category(event)
                if event.caption:
                    text += f' "{event.caption}"'

            await self.log_action(user_id, username, action, text)

        elif isinstance(event, CallbackQuery):
            call_data = event.data or "callback"
            await self.log_action(user_id, username, "button_click", call_data)

        return await handler(event, data)


# Global singleton instance
user_action_logging_middleware = UserActionLoggingMiddleware()

__all__ = [
    "UserActionLoggingMiddleware",
    "format_capped_text",
    "get_media_category",
    "user_action_logging_middleware",
]
