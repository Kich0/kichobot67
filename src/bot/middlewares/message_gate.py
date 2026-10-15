"""Message Gate and Anti-Spam Middleware for aiogram 3.

Implements:
1. Sliding window rate-limiting (500ms between interactions).
2. Anti-spam detection (8 requests within 10s -> 60s temporary mute).
3. Administrator bans and mutes check via BlackList collection.
4. Non-text message rejection in private chats.
5. Message length constraint (250 chars max for plain text queries).
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.core.constants import (
    ANTI_SPAM_BAN_DURATION,
    ANTI_SPAM_MAX_REQUESTS,
    ANTI_SPAM_WINDOW,
    MESSAGE_GATE_THROTTLE_INTERVAL,
)
from src.core.logging import get_logger

logger = get_logger("message_gate")


class MessageGateMiddleware(BaseMiddleware):
    """Enforces rate limiting, spam prevention, and access policies."""

    def __init__(
        self,
        throttle_interval: float = MESSAGE_GATE_THROTTLE_INTERVAL,
        spam_window: float = ANTI_SPAM_WINDOW,
        spam_max_requests: int = ANTI_SPAM_MAX_REQUESTS,
        ban_duration: float = ANTI_SPAM_BAN_DURATION,
        max_text_length: int = 250,
    ) -> None:
        super().__init__()
        self.throttle_interval = throttle_interval
        self.spam_window = spam_window
        self.spam_max_requests = spam_max_requests
        self.ban_duration = ban_duration
        self.max_text_length = max_text_length

        self._user_last_interaction: dict[int, float] = {}
        self._user_request_timestamps: dict[int, list[float]] = {}
        self._banned_users: dict[int, float] = {}
        self._ban_warning_sent: dict[int, bool] = {}

    def is_user_banned(self, user_id: int) -> bool:
        """Returns True if user is currently under temporary spam ban."""
        now = time.time()
        expire_time = self._banned_users.get(user_id)
        if expire_time:
            if now < expire_time:
                return True
            del self._banned_users[user_id]
            self._ban_warning_sent.pop(user_id, None)
        return False

    def clear_state(self) -> None:
        """Resets in-memory tracking structures (useful for tests)."""
        self._user_last_interaction.clear()
        self._user_request_timestamps.clear()
        self._banned_users.clear()
        self._ban_warning_sent.clear()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        now = time.time()

        user = getattr(event, "from_user", None)
        if not user or not user.id:
            return await handler(event, data)

        user_id = user.id

        # 1. Check temporary spam ban (applies strictly to incoming text messages)
        if isinstance(event, Message) and self.is_user_banned(user_id):
            return None

        # 2. Check BlackList in MongoDB (admin ban/mute)
        try:
            from src.database.models.black_list import BlackList

            restriction = await BlackList.find_one({"userId": user_id})
            if restriction:
                is_expired = restriction.until and restriction.until.timestamp() < now
                if not is_expired:
                    if restriction.type == "ban":
                        if isinstance(event, CallbackQuery):
                            await event.answer(
                                "⛔ Ваш доступ к боту заблокирован администратором.",
                                show_alert=True,
                            )
                        elif isinstance(event, Message) and event.chat.type == "private":
                            await event.answer(
                                "⛔ <b>Ваш доступ к боту заблокирован администратором.</b>",
                                parse_mode="HTML",
                            )
                        return None
                    elif restriction.type == "mute":
                        if isinstance(event, CallbackQuery):
                            await event.answer("🔇 Вам выдан мут администратором.", show_alert=True)
                        return None
        except Exception as e:
            logger.debug(f"BlackList check skipped or errored: {e}")

        # 3. Sliding window spam detection (STRICTLY for Message text spam; button clicks do not cause 60s bans)
        if isinstance(event, Message):
            timestamps = self._user_request_timestamps.setdefault(user_id, [])
            timestamps.append(now)
            timestamps = [t for t in timestamps if now - t < self.spam_window]
            self._user_request_timestamps[user_id] = timestamps

            if len(timestamps) > self.spam_max_requests:
                self._banned_users[user_id] = now + self.ban_duration
                logger.warning(
                    f"[GATE] User {user_id} BANNED for {int(self.ban_duration)}s "
                    f"({len(timestamps)} msgs in {self.spam_window}s)"
                )
                if not self._ban_warning_sent.get(user_id):
                    self._ban_warning_sent[user_id] = True
                    ban_sec = int(self.ban_duration)
                    warning_text = (
                        f"🚫 <b>Блокировка на {ban_sec} секунд</b>\n\n"
                        "Вы отправили слишком много сообщений.\n"
                        "⏳ Бот будет игнорировать вас до окончания блокировки.\n\n"
                        "<i>Пожалуйста, не спамьте.</i>"
                    )
                    await event.answer(warning_text, parse_mode="HTML")
                return None

            # 4. Message throttle interval
            last_interaction = self._user_last_interaction.get(user_id)
            if last_interaction and (now - last_interaction < self.throttle_interval):
                return None
            self._user_last_interaction[user_id] = now

        # 5. Media & message length validations for private messages
        if isinstance(event, Message) and event.chat.type == "private":
            # Reject non-text messages
            if not event.text:
                try:
                    await event.answer(
                        "⛔ <b>Бот принимает только текст</b>\n\n"
                        "Стикеры, фото, видео, файлы и голосовые\nне поддерживаются.\n\n"
                        "Нажмите /start для начала.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return None

            # Reject messages exceeding maximum character limit
            if len(event.text) > self.max_text_length and not event.text.startswith("/"):
                try:
                    await event.answer(
                        f"⛔ <b>Слишком длинное сообщение</b>\n\n"
                        f"Максимум {self.max_text_length} символов.\nИспользуйте кнопки 👇",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return None

        return await handler(event, data)


__all__ = ["MessageGateMiddleware"]
