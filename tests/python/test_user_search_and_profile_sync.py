"""Unit tests for user profile syncing on-the-fly and multi-attribute search.

Verifies:
1. User profile attributes (username, firstName, lastName) are kept fresh via touch_user_profile.
2. Search finds users by Telegram ID, current @username, historical @username from logs, or name.
3. Multiple matches return candidate list in admin /get_user handler.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.database.models.user import User
from src.database.models.user_action import UserAction
from src.services.user_service import user_service
from src.bot.handlers.admin_router import handle_get_user


@pytest.mark.asyncio
async def test_search_user_by_id(mock_db):
    """Finds user directly by numeric ID or ID string."""
    u = User(userId=7341879283, username="rinchori", firstName="Deadly drive")
    await u.insert()

    # Search by int
    res_int = await user_service.search_users(7341879283)
    assert len(res_int) == 1
    assert res_int[0].user_id == 7341879283

    # Search by string
    res_str = await user_service.search_users("7341879283")
    assert len(res_str) == 1
    assert res_str[0].user_id == 7341879283

    # Single find_user
    single = await user_service.find_user(7341879283)
    assert single is not None
    assert single.username == "rinchori"


@pytest.mark.asyncio
async def test_search_user_by_current_username(mock_db):
    """Finds user by @username (with or without leading @, case-insensitive)."""
    u = User(userId=12345, username="student_alex", firstName="Alex")
    await u.insert()

    res1 = await user_service.search_users("student_alex")
    assert len(res1) == 1
    assert res1[0].user_id == 12345

    res2 = await user_service.search_users("@STUDENT_ALEX")
    assert len(res2) == 1
    assert res2[0].user_id == 12345


@pytest.mark.asyncio
async def test_search_user_by_historical_username(mock_db):
    """Finds user by their previous username from UserAction history after username changed."""
    # User currently has new username
    u = User(userId=99999, username="new_nick_2026", firstName="Changed Person")
    await u.insert()

    # Past log has old username
    act = UserAction(
        userId=99999,
        username="old_famous_nick",
        action="command",
        text="/start",
    )
    await act.insert()

    # Searching by old username should locate the user
    found = await user_service.find_user("@old_famous_nick")
    assert found is not None
    assert found.user_id == 99999
    assert found.username == "new_nick_2026"


@pytest.mark.asyncio
async def test_search_user_by_name(mock_db):
    """Finds user by firstName, lastName, or multi-word combined name."""
    u1 = User(userId=77701, username="gamer1", firstName="Deadly drive")
    u2 = User(userId=77702, username="damir_k", firstName="Дамир", lastName="Сейтов")
    await u1.insert()
    await u2.insert()

    # Match exact multi-word first name
    res_deadly = await user_service.search_users("Deadly drive")
    assert len(res_deadly) == 1
    assert res_deadly[0].user_id == 77701

    # Match first and last name combined
    res_damir = await user_service.search_users("Дамир Сейтов")
    assert len(res_damir) == 1
    assert res_damir[0].user_id == 77702

    # Match single word
    res_word = await user_service.search_users("Сейтов")
    assert len(res_word) == 1
    assert res_word[0].user_id == 77702


@pytest.mark.asyncio
async def test_search_user_multiple_matches(mock_db):
    """Returns multiple users when a query matches several profiles."""
    u1 = User(userId=88801, username="alikhan_1", firstName="Алихан")
    u2 = User(userId=88802, username="alikhan_2", firstName="Алихан")
    await u1.insert()
    await u2.insert()

    results = await user_service.search_users("Алихан")
    assert len(results) == 2
    ids = {r.user_id for r in results}
    assert ids == {88801, 88802}


@pytest.mark.asyncio
async def test_touch_user_profile_syncs_db(mock_db):
    """touch_user_profile updates changed username/firstName in DB without blocking."""
    user_service.clear_cache()
    u = User(userId=55555, username="old_user", firstName="Old Name")
    await u.insert()

    # 1. First touch with new username and name
    user_service.touch_user_profile(
        user_id=55555,
        username="fresh_user",
        first_name="Fresh Name",
    )
    # Give background task a moment to finish
    await asyncio.sleep(0.05)

    updated = await User.find_one({"userId": 55555})
    assert updated.username == "fresh_user"
    assert updated.first_name == "Fresh Name"

    # 2. Touch with unchanged profile hits memory cache (no DB write needed)
    with patch.object(user_service, "_sync_profile_to_db", new_callable=AsyncMock) as mock_sync:
        user_service.touch_user_profile(
            user_id=55555,
            username="fresh_user",
            first_name="Fresh Name",
        )
        assert mock_sync.call_count == 0


@pytest.mark.asyncio
async def test_handle_get_user_multiple_candidates(mock_db):
    """When multiple users match query in /get_user, displays candidate list with /get_user [ID]."""
    u1 = User(userId=11101, username="test_u1", firstName="Мария")
    u2 = User(userId=11102, username="test_u2", firstName="Мария")
    await u1.insert()
    await u2.insert()

    msg = MagicMock()
    msg.from_user.id = 1516988645  # Admin
    msg.text = "/get_user Мария"
    msg.answer = AsyncMock()

    with patch("src.bot.handlers.admin_router._check_admin", new_callable=AsyncMock, return_value=True):
        await handle_get_user(msg)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "Найдено пользователей (2)" in reply_text
    assert "/get_user 11101" in reply_text
    assert "/get_user 11102" in reply_text


@pytest.mark.asyncio
async def test_handle_get_user_live_telegram_refresh(mock_db):
    """When /get_user displays user, it live-refreshes username via bot.get_chat to prevent pointing to a stranger."""
    u = User(userId=44401, username="very_old_nick", firstName="Old Name")
    await u.insert()

    msg = MagicMock()
    msg.from_user.id = 1516988645  # Admin
    msg.text = "/get_user 44401"
    msg.answer = AsyncMock()

    mock_chat = MagicMock()
    mock_chat.username = "real_new_nick"
    mock_chat.first_name = "New Live Name"
    mock_chat.last_name = None
    msg.bot.get_chat = AsyncMock(return_value=mock_chat)

    with patch("src.bot.handlers.admin_router._check_admin", new_callable=AsyncMock, return_value=True):
        await handle_get_user(msg)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "@real_new_nick" in reply_text
    assert "very_old_nick" not in reply_text

    # Verify DB was updated
    db_u = await User.find_one({"userId": 44401})
    assert db_u.username == "real_new_nick"
    assert db_u.first_name == "New Live Name"

