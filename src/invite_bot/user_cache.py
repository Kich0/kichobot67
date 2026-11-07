"""In-memory cache mapping Telegram username <-> user_id for @Karu_proxy_bot.

Populated dynamically from chat messages and join events so admin commands
(/mute @username, /kick @username, etc.) can resolve usernames to numeric IDs.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

MAX_CACHED_USERS = 2000

# username.lower() -> {"user_id": int, "username": str, "first_name": str}
_users_by_username: OrderedDict[str, dict[str, Any]] = OrderedDict()
# user_id -> {"user_id": int, "username": str | None, "first_name": str}
_users_by_id: OrderedDict[int, dict[str, Any]] = OrderedDict()


def record_user(
    user_id: int | None,
    username: str | None = None,
    first_name: str | None = None,
) -> dict[str, Any] | None:
    """Stores or updates a user in the in-memory cache."""
    if not user_id:
        return None

    entry = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name or "",
    }
    _users_by_id[user_id] = entry
    while len(_users_by_id) > MAX_CACHED_USERS:
        _users_by_id.popitem(last=False)

    if username:
        clean = username.lstrip("@").strip().lower()
        if clean:
            _users_by_username[clean] = entry
            while len(_users_by_username) > MAX_CACHED_USERS:
                _users_by_username.popitem(last=False)

    return entry


def get_user_by_username(username: str) -> dict[str, Any] | None:
    """Finds cached user by @username (case-insensitive)."""
    clean = username.lstrip("@").strip().lower()
    return _users_by_username.get(clean)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Finds cached user by numeric ID."""
    return _users_by_id.get(user_id)


def clear_user_cache() -> None:
    """Resets the cache (used in tests)."""
    _users_by_username.clear()
    _users_by_id.clear()


__all__ = [
    "MAX_CACHED_USERS",
    "clear_user_cache",
    "get_user_by_id",
    "get_user_by_username",
    "record_user",
]
