"""Unit tests for /stat comprehensive statistics, user toll paywall, and admin security."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database.models.user import User
from src.database.models.user_action import UserAction
from src.database.models.user_toll import UserToll
from src.services.user_activity_service import ComprehensiveStats, DailyStatEntry, user_activity_service
from src.services.user_service import user_service
from src.services.user_toll_service import user_toll_service


@pytest.mark.asyncio
async def test_stat_message_format():
    """Validates the exact 7-day format of statistics message matching Node.js commit 25601dc."""
    stats = ComprehensiveStats(
        active_users_today=15,
        actions_today=120,
        registered_users_today=3,
        week_unique_users_count=48,
        week_total_actions_count=750,
        week_registered_users_count=12,
        daily_activity=[DailyStatEntry(date_str="14.10", count=10)],
        daily_actions=[DailyStatEntry(date_str="14.10", count=95)],
        daily_registrations=[DailyStatEntry(date_str="14.10", count=2)],
    )

    formatted = user_activity_service.format_stat_message(stats)

    assert "Статистика ежедневного онлайна:" in formatted
    assert "Сегодня: 15" in formatted
    assert "14.10: 10" in formatted
    assert "Всего уникальных пользователей за неделю: 48" in formatted
    assert "Статистика ежедневных действий:" in formatted
    assert "Сегодня: 120" in formatted
    assert "Всего действий за неделю: 750" in formatted
    assert "Статистика ежедневных регистраций:" in formatted
    assert "Сегодня: 3" in formatted
    assert "Всего зареганных пользователей за неделю: 12" in formatted


@pytest.mark.asyncio
async def test_user_toll_service_crud(mock_db):
    """Tests enabling, checking, consuming credits, and disabling toll for a user."""
    user_toll_service.clear_cache()
    target_user_id = 999888777

    # Initially not tolled
    assert not user_toll_service.is_user_tolled(target_user_id)

    # Enable toll with 9000 stars
    await user_toll_service.enable_toll(target_user_id, star_price=9000, username="test_tolled")
    assert user_toll_service.is_user_tolled(target_user_id)
    assert user_toll_service.get_toll_price(target_user_id) == 9000
    assert not user_toll_service.has_paid_credit(target_user_id)

    # Grant 1 paid credit
    await user_toll_service.add_paid_credit(target_user_id, count=1)
    assert user_toll_service.has_paid_credit(target_user_id)

    # Consume credit
    consumed = user_toll_service.consume_paid_credit(target_user_id)
    assert consumed is True
    assert not user_toll_service.has_paid_credit(target_user_id)

    # Second consumption fails
    consumed_again = user_toll_service.consume_paid_credit(target_user_id)
    assert consumed_again is False

    # Disable toll
    await user_toll_service.disable_toll(target_user_id)
    assert not user_toll_service.is_user_tolled(target_user_id)


@pytest.mark.asyncio
async def test_admin_router_security_rejection(mock_db):
    """Verifies that non-admin users receive the security denial message on admin commands."""
    from src.bot.handlers.admin_router import handle_paywall_command, handle_stat

    fake_message = MagicMock()
    fake_message.from_user.id = 123454321
    fake_message.text = "/stat"
    fake_message.answer = AsyncMock()

    await handle_stat(fake_message)

    fake_message.answer.assert_called_once_with("🚫 У вас нет доступа к этой прекрасной команде!")


@pytest.mark.asyncio
async def test_user_service_duplicate_key_race_condition(mock_db):
    """Verifies that UserService handles DuplicateKeyError gracefully on concurrent inserts."""
    user_id = 77712345
    # Concurrent get_or_create_user calls for a brand new user_id
    res1, res2 = await asyncio.gather(
        user_service.get_or_create_user(user_id=user_id, username="first_runner"),
        user_service.get_or_create_user(user_id=user_id, username="second_runner"),
    )
    assert res1 is not None
    assert res2 is not None
    assert res1.user_id == user_id
    assert res2.user_id == user_id

    # Concurrent set_user_language calls for a brand new user_id
    user_id_2 = 88812345
    await asyncio.gather(
        user_service.set_user_language(user_id=user_id_2, language="kz"),
        user_service.set_user_language(user_id=user_id_2, language="ru"),
    )
    user_doc = await User.find_one({"userId": user_id_2})
    assert user_doc is not None
    assert user_doc.user_id == user_id_2
    assert user_doc.language in ("ru", "kz")
