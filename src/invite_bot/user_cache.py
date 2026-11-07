"""In-memory cache mapping Telegram username <-> user_id for @Karu_proxy_bot.

Populated dynamically from chat messages and join events so admin commands
(/mute @username, /kick @username, etc.) can resolve usernames to numeric IDs.
Also tracks profile changes (username, name) to generate 'profile_updated' events.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_CACHED_USERS = 2000
PROFILE_UPDATE_COOLDOWN = timedelta(minutes=10)

# username.lower() -> {"user_id": int, "username": str, "first_name": str, "last_name": str}
_users_by_username: OrderedDict[str, dict[str, Any]] = OrderedDict()
# user_id -> {"user_id": int, "username": str | None, "first_name": str, "last_name": str}
_users_by_id: OrderedDict[int, dict[str, Any]] = OrderedDict()
# user_id -> datetime of last profile_updated event emitted
_last_profile_emit: dict[int, datetime] = {}


def record_user(
    user_id: int | None,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, Any] | None:
    """Stores or updates a user in the in-memory cache."""
    if not user_id:
        return None

    entry = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name or "",
        "last_name": last_name or "",
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


def check_user_profile_changed(
    user_id: int,
    username: str | None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Detects if a user's username, first_name or last_name changed compared to cache.

    Returns:
        (has_changed, old_profile_or_None)
    """
    old = _users_by_id.get(user_id)
    if old is None:
        # First time seen: record, but not considered a change
        record_user(user_id, username, first_name, last_name)
        return False, None

    old_u = old.get("username")
    old_f = old.get("first_name") or ""
    old_l = old.get("last_name") or ""

    new_f = first_name or ""
    new_l = last_name or ""

    changed = (old_u != username) or (old_f != new_f) or (old_l != new_l)
    if not changed:
        return False, old

    # Check cooldown
    now = datetime.now(timezone.utc)
    last_emit = _last_profile_emit.get(user_id)
    if last_emit and (now - last_emit) < PROFILE_UPDATE_COOLDOWN:
        # Changed, but throttled to avoid event flooding
        record_user(user_id, username, first_name, last_name)
        return False, old

    _last_profile_emit[user_id] = now
    # Update cache with new details
    record_user(user_id, username, first_name, last_name)
    return True, old


def get_user_by_username(username: str) -> dict[str, Any] | None:
    """Finds cached user by @username (case-insensitive)."""
    clean = username.lstrip("@").strip().lower()
    return _users_by_username.get(clean)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Finds cached user by numeric ID."""
    return _users_by_id.get(user_id)


def get_all_tracked_user_ids() -> list[int]:
    """Returns all user IDs currently in the in-memory cache."""
    return list(_users_by_id.keys())


def clear_user_cache() -> None:
    """Resets the cache (used in tests)."""
    _users_by_username.clear()
    _users_by_id.clear()
    _last_profile_emit.clear()


__all__ = [
    "MAX_CACHED_USERS",
    "PROFILE_UPDATE_COOLDOWN",
    "check_user_profile_changed",
    "clear_user_cache",
    "get_all_tracked_user_ids",
    "get_user_by_id",
    "get_user_by_username",
    "record_user",
]
