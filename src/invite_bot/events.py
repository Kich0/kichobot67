"""In-memory join-event buffer and signed avatar URLs for the invite bot.

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

recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
links_registry: OrderedDict[str, dict[str, Any]] = OrderedDict()


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
    }
    links_registry[invite_link] = entry
    while len(links_registry) > MAX_LINKS:
        links_registry.popitem(last=False)
    return entry


def get_link_status(invite_link: str) -> dict[str, Any] | None:
    """Returns tracking info and status for a link: 'active', 'used', or 'expired'."""
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
    """Returns buffered events (oldest first), optionally only those with joined_at > since.

    ISO-8601 strings with the same timezone offset compare correctly as strings;
    all events are produced in Asia/Almaty (+05:00).
    """
    events = list(recent_events)
    if since:
        events = [e for e in events if str(e.get("joined_at", "")) > since]
    return events


def clear_events() -> None:
    recent_events.clear()
    links_registry.clear()


__all__ = [
    "MAX_EVENTS",
    "MAX_LINKS",
    "add_event",
    "build_avatar_url",
    "clear_events",
    "get_events",
    "get_link_status",
    "links_registry",
    "mark_link_used",
    "recent_events",
    "register_link",
    "sign_file_id",
    "verify_file_id_signature",
]
