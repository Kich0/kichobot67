"""In-memory join/leave/ban event buffer and link lifecycle registry.

Events are kept in RAM only (no MongoDB): the partner site either receives them
via push (INVITE_SITE_WEBHOOK_URL) or pulls them from GET /api/invite/events.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from datetime import datetime
import hashlib
import hmac
from typing import Any
import urllib.parse

from src.core.config import settings
from src.core.constants import TIMEZONE

MAX_EVENTS = 500
MAX_LINKS = 1000
MAX_USERS = 2000

recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
links_registry: OrderedDict[str, dict[str, Any]] = OrderedDict()
user_links: OrderedDict[int, str] = OrderedDict()
user_statuses: OrderedDict[int, dict[str, Any]] = OrderedDict()


def register_link(
    invite_link: str,
    chat_id: int,
    expire_date: str | None = None,
) -> dict[str, Any]:
    """Registers a newly created invite link with initial 'active' status."""
    entry = {
        "invite_link": invite_link,
        "chat_id": chat_id,
        "status": "active",
        "member_limit": 1,
        "created_at": datetime.now(TIMEZONE).isoformat(),
        "expire_date": expire_date,
        "used_at": None,
        "used_by": None,
        "left_at": None,
        "banned_at": None,
    }
    links_registry[invite_link] = entry
    while len(links_registry) > MAX_LINKS:
        links_registry.popitem(last=False)
    return entry


def mark_link_used(invite_link: str, user_payload: dict[str, Any]) -> dict[str, Any]:
    """Marks an invite link as 'used' and records the user who joined with it."""
    entry = links_registry.get(invite_link)
    used_at = user_payload.get("joined_at") or datetime.now(TIMEZONE).isoformat()
    used_by = {
        "telegram_id": user_payload.get("telegram_id"),
        "username": user_payload.get("username"),
        "first_name": user_payload.get("first_name"),
        "last_name": user_payload.get("last_name"),
    }
    tg_id = user_payload.get("telegram_id")
    if tg_id:
        user_links[tg_id] = invite_link
        while len(user_links) > MAX_USERS:
            user_links.popitem(last=False)
        user_statuses[tg_id] = {
            "status": "member",
            "in_chat": True,
            "can_reissue": False,
            "invite_link": invite_link,
            "updated_at": used_at,
        }
        while len(user_statuses) > MAX_USERS:
            user_statuses.popitem(last=False)

    if entry is not None:
        entry["status"] = "used"
        entry["used_at"] = used_at
        entry["used_by"] = used_by
        return entry

    # Link might not be in RAM if server restarted, record it now
    entry = {
        "invite_link": invite_link,
        "chat_id": user_payload.get("chat_id"),
        "status": "used",
        "member_limit": 1,
        "created_at": None,
        "expire_date": None,
        "used_at": used_at,
        "used_by": used_by,
        "left_at": None,
        "banned_at": None,
    }
    links_registry[invite_link] = entry
    while len(links_registry) > MAX_LINKS:
        links_registry.popitem(last=False)
    return entry


def mark_link_left(user_id: int, user_payload: dict[str, Any]) -> str | None:
    """Marks user as left and transitions their last link status to 'left'."""
    updated_at = user_payload.get("updated_at") or datetime.now(TIMEZONE).isoformat()
    invite_link = user_links.get(user_id) or user_payload.get("invite_link")
    if invite_link and invite_link in links_registry:
        links_registry[invite_link]["status"] = "left"
        links_registry[invite_link]["left_at"] = updated_at

    user_statuses[user_id] = {
        "status": "left",
        "in_chat": False,
        "can_reissue": True,
        "invite_link": invite_link,
        "updated_at": updated_at,
    }
    while len(user_statuses) > MAX_USERS:
        user_statuses.popitem(last=False)
    return invite_link


def mark_link_banned(user_id: int, user_payload: dict[str, Any]) -> str | None:
    """Marks user as banned and transitions their last link status to 'banned'."""
    updated_at = user_payload.get("updated_at") or datetime.now(TIMEZONE).isoformat()
    invite_link = user_links.get(user_id) or user_payload.get("invite_link")
    if invite_link and invite_link in links_registry:
        links_registry[invite_link]["status"] = "banned"
        links_registry[invite_link]["banned_at"] = updated_at

    user_statuses[user_id] = {
        "status": "banned",
        "in_chat": False,
        "can_reissue": False,
        "invite_link": invite_link,
        "updated_at": updated_at,
    }
    while len(user_statuses) > MAX_USERS:
        user_statuses.popitem(last=False)
    return invite_link


def get_user_status_entry(user_id: int) -> dict[str, Any] | None:
    """Returns cached lifecycle status for a user."""
    return user_statuses.get(user_id)


def get_link_status(invite_link: str) -> dict[str, Any] | None:
    """Returns tracking info and status for a link: 'active', 'used', 'left', 'banned', or 'expired'."""
    entry = links_registry.get(invite_link)
    if entry is None:
        return None

    if entry["status"] == "active" and entry.get("expire_date"):
        try:
            exp = datetime.fromisoformat(entry["expire_date"])
            if datetime.now(TIMEZONE) > exp:
                entry["status"] = "expired"
        except Exception:
            pass
    return entry


def sign_file_id(file_id: str) -> str:
    """Returns a 32-hex HMAC-SHA256 signature of a Telegram file_id."""
    key = (settings.INVITE_API_KEY or "").encode()
    return hmac.new(key, file_id.encode(), hashlib.sha256).hexdigest()[:32]


def verify_file_id_signature(file_id: str, sig: str | None) -> bool:
    """Constant-time check of an avatar URL signature."""
    if not sig or not settings.INVITE_API_KEY:
        return False
    return hmac.compare_digest(sig, sign_file_id(file_id))


def build_avatar_url(file_id: str | None) -> str | None:
    """Builds a public signed URL to the avatar proxy endpoint."""
    if not file_id or not settings.WEBHOOK_DOMAIN or not settings.INVITE_API_KEY:
        return None
    domain = settings.WEBHOOK_DOMAIN.rstrip("/")
    quoted = urllib.parse.quote(file_id, safe="")
    return f"{domain}/api/invite/avatar/{quoted}?sig={sign_file_id(file_id)}"


def add_event(payload: dict[str, Any]) -> None:
    recent_events.append(payload)


def get_events(since: str | None = None) -> list[dict[str, Any]]:
    """Returns buffered events (oldest first), optionally only those with timestamp > since."""
    events = list(recent_events)
    if since:
        events = [
            e
            for e in events
            if str(e.get("joined_at") or e.get("updated_at") or "") > since
        ]
    return events


def clear_events() -> None:
    recent_events.clear()
    links_registry.clear()
    user_links.clear()
    user_statuses.clear()


__all__ = [
    "MAX_EVENTS",
    "MAX_LINKS",
    "MAX_USERS",
    "add_event",
    "build_avatar_url",
    "clear_events",
    "get_events",
    "get_link_status",
    "get_user_status_entry",
    "links_registry",
    "mark_link_banned",
    "mark_link_left",
    "mark_link_used",
    "recent_events",
    "register_link",
    "sign_file_id",
    "user_links",
    "user_statuses",
    "verify_file_id_signature",
]
