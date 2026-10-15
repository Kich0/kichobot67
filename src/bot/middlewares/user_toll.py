"""User Toll Callback Interceptor Middleware.

Enforces Telegram Stars paywall on inline buttons for targeted users.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, LabeledPrice, TelegramObject

from src.bot.middlewares.user_action_logger import user_action_logging_middleware
from src.core.logging import get_logger
from src.services.user_toll_service import user_toll_service

logger = get_logger("user_toll_middleware")


class UserTollMiddleware(BaseMiddleware):
    """Intercepts callback button presses for users in paid button mode."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            user = event.from_user
            if user and user.id:
                user_id = user.id
                await user_toll_service.ensure_loaded()
                if user_toll_service.is_user_tolled(user_id):
                    if user_toll_service.has_paid_credit(user_id):
                        # Consume 1 paid credit and permit click
                        user_toll_service.consume_paid_credit(user_id)
                        await user_action_logging_middleware.log_action(
                            user_id,
                            user.username,
                            "toll_button_paid_click",
                            f"Использовано оплаченное нажатие для «{event.data}»",
                        )
                    else:
                        star_price = user_toll_service.get_toll_price(user_id)
                        formatted_price = f"{star_price:,}".replace(",", " ")
                        try:
                            await event.answer(
                                f"⭐ Эта кнопка платная! Стоимость: {formatted_price} звёзд Telegram.",
                                show_alert=True,
                            )
                        except Exception:
                            pass

                        bot = data.get("bot") or event.bot
                        chat_id = (
                            event.message.chat.id
                            if event.message and event.message.chat
                            else user_id
                        )
                        if bot:
                            try:
                                await bot.send_invoice(
                                    chat_id=chat_id,
                                    title="⭐ Платная кнопка",
                                    description=f"Для совершения этого действия требуется оплата: {formatted_price} ⭐ Telegram Stars.",
                                    payload=f"toll_btn_{user_id}_{int(time.time() * 1000)}",
                                    provider_token="",  # Telegram Stars requires empty string
                                    currency="XTR",
                                    prices=[LabeledPrice(label="Нажатие кнопки", amount=star_price)],
                                )
                            except Exception as e:
                                logger.error(f"[UserToll] Error sending stars invoice: {e}")

                        await user_action_logging_middleware.log_action(
                            user_id,
                            user.username,
                            "toll_button_blocked",
                            f"Клик «{event.data}» — выставлен счёт на {star_price} ⭐",
                        )
                        return None

        return await handler(event, data)


user_toll_middleware = UserTollMiddleware()

__all__ = ["UserTollMiddleware", "user_toll_middleware"]
