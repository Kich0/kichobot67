"""In-memory join-event buffer and signed avatar URLs for the invite bot.

Events are kept in RAM only (no MongoDB): the partner site either receives them
via push (INVITE_SITE_WEBHOOK_URL) or pulls them from GET /api/invite/events.
"""

from __future__ import annotations

from collections import deque
import hashlib
import hmac
from typing import Any
import urllib.parse

from src.core.config import settings

MAX_EVENTS = 500

recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)


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


__all__ = [
    "MAX_EVENTS",
    "add_event",
    "build_avatar_url",
    "clear_events",
    "get_events",
    "recent_events",
    "sign_file_id",
    "verify_file_id_signature",
]
