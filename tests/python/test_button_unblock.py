"""Unit tests verifying that CallbackQuery clicks are not blocked by the spam ban or throttle."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from src.bot.middlewares.message_gate import MessageGateMiddleware


@pytest.mark.asyncio
async def test_callback_query_not_blocked_by_rapid_clicks():
    """Rapid inline button clicks must never trigger a 60-second spam ban or be dropped."""
    middleware = MessageGateMiddleware()
    user = User(id=777111222, is_bot=False, first_name="Clicker")

    handler = AsyncMock(return_value="handled")

    # Simulate 20 rapid callback query button clicks
    for _ in range(20):
        call = MagicMock(spec=CallbackQuery)
        call.from_user = user
        call.message = MagicMock()
        call.answer = AsyncMock()

        result = await middleware(handler, call, {})
        assert result == "handled"

    assert handler.call_count == 20
    # User should NOT be under temporary spam ban
    assert not middleware.is_user_banned(777111222)


@pytest.mark.asyncio
async def test_message_flood_triggers_temporary_spam_ban():
    """Excessive text message flood (> 5 messages in 3s) must trigger the temporary 60s ban."""
    # Set throttle_interval=0 to test pure flood detection logic
    middleware = MessageGateMiddleware(throttle_interval=0.0)
    user = User(id=888222333, is_bot=False, first_name="Spammer")
    chat = Chat(id=888222333, type="private")

    handler = AsyncMock(return_value="handled")

    # Send 5 messages (within threshold)
    for _ in range(5):
        msg = MagicMock(spec=Message)
        msg.from_user = user
        msg.chat = chat
        msg.text = "Hello bot"
        msg.answer = AsyncMock()
        result = await middleware(handler, msg, {})
        assert result == "handled"

    # 6th message triggers spam ban
    msg6 = MagicMock(spec=Message)
    msg6.from_user = user
    msg6.chat = chat
    msg6.text = "Hello spam"
    msg6.answer = AsyncMock()

    result6 = await middleware(handler, msg6, {})
    assert result6 is None  # Blocked
    assert middleware.is_user_banned(888222333)
    msg6.answer.assert_called_once()
    assert "слишком много сообщений" in msg6.answer.call_args[0][0]
    assert "60 секунд" in msg6.answer.call_args[0][0]
