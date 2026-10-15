"""Payment handlers router for Telegram Stars.

Handles:
- pre_checkout_query: immediate confirmation.
- message(F.successful_payment): grants paid button credits and notifies user.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery

from src.bot.middlewares.user_action_logger import user_action_logging_middleware
from src.core.logging import get_logger
from src.services.user_toll_service import user_toll_service

logger = get_logger("payment_router")
payment_router = Router(name="payment_router")


@payment_router.pre_checkout_query()
async def handle_pre_checkout_query(query: PreCheckoutQuery) -> None:
    """Confirms pre-checkout queries for Telegram Stars purchases."""
    try:
        user_id = query.from_user.id if query.from_user else 0
        logger.info(f"[Payment] Pre-checkout query from user {user_id} for {query.total_amount} {query.currency}")
        await query.answer(ok=True)
    except Exception as e:
        logger.error(f"[Payment] Pre-checkout answer error: {e}")
        try:
            await query.answer(ok=False, error_message="Не удалось подтвердить платёж.")
        except Exception:
            pass


@payment_router.message(F.successful_payment)
async def handle_successful_payment(message: Message) -> None:
    """Processes confirmed Telegram Stars payments."""
    payment = message.successful_payment
    if not payment:
        return

    user_id = message.from_user.id if message.from_user else (message.chat.id if message.chat else 0)
    username = message.from_user.username if message.from_user else None

    logger.info(
        f"[Payment] Successful payment from user {user_id}: "
        f"{payment.total_amount} {payment.currency} (charge_id: {payment.telegram_payment_charge_id})"
    )

    # Add 1 paid click credit
    await user_toll_service.add_paid_credit(user_id, count=1)

    # Log action
    await user_action_logging_middleware.log_action(
        user_id,
        username,
        "stars_payment_success",
        f"Оплачено {payment.total_amount} ⭐ звёзд (ID транзакции: {payment.telegram_payment_charge_id})",
    )

    formatted_amount = f"{payment.total_amount:,}".replace(",", " ")
    await message.answer(
        f"🌟 <b>Оплата {formatted_amount} ⭐ звёзд успешно получена!</b>\n\n"
        "Спасибо за оплату! Нажатие кнопки разблокировано, теперь нажмите её ещё раз.",
        parse_mode="HTML",
    )


__all__ = ["handle_pre_checkout_query", "handle_successful_payment", "payment_router"]
