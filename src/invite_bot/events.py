"""In-memory join/leave/ban event buffer and link lifecycle registry.

Hybrid RAM + MongoDB persistence:
- Fast in-memory cache for low-latency lookups.
- Background asynchronous persistence to MongoDB (InviteLink, InviteEvent)
  for disaster recovery and cross-restart persistence ('2-я защита').
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime
import hashlib
import hmac
from typing import Any
import urllib.parse

from src.core.config import settings
from src.core.constants import TIMEZONE
from src.core.logging import get_logger

logger = get_logger("invite_bot.events")

MAX_EVENTS = 500
MAX_LINKS = 1000
MAX_USERS = 2000

recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
links_registry: OrderedDict[str, dict[str, Any]] = OrderedDict()
user_links: OrderedDict[int, str] = OrderedDict()
user_statuses: OrderedDict[int, dict[str, Any]] = OrderedDict()


def _is_db_ready() -> bool:
    try:
        from src.database.connection import get_client
        return get_client() is not None
    except Exception:
        return False


def _schedule_bg(coro_fn, *args, **kwargs) -> None:
    if not _is_db_ready():
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro_fn(*args, **kwargs))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    except RuntimeError:
        pass


async def _db_save_link(entry: dict[str, Any]) -> None:
    if not _is_db_ready():
        return
    try:
        from src.database.models.invite_link import InviteLink
        link_url = entry.get("invite_link")
        if not link_url:
            return
        existing = await InviteLink.find_one(InviteLink.invite_link == link_url)
        if existing is None:
            await InviteLink(**entry).insert()
        else:
            await existing.set({
                "status": entry.get("status"),
                "used_at": entry.get("used_at"),
                "used_by": entry.get("used_by"),
                "left_at": entry.get("left_at"),
                "banned_at": entry.get("banned_at"),
            })
    except Exception as e:
        logger.warning(f"[InviteBot] _db_save_link error: {e}")


async def _db_save_event(payload: dict[str, Any]) -> None:
    if not _is_db_ready():
        return
    try:
        from src.database.models.invite_event import InviteEvent
        await InviteEvent(
            event=payload.get("event", "joined"),
            status=payload.get("status", "used"),
            telegram_id=payload.get("telegram_id", 0),
            username=payload.get("username"),
            first_name=payload.get("first_name"),
            last_name=payload.get("last_name"),
            old_username=payload.get("old_username"),
            avatar_url=payload.get("avatar_url"),
            avatar_file_id=payload.get("avatar_file_id"),
            chat_id=payload.get("chat_id", 0),
            invite_link=payload.get("invite_link"),
        ).insert()
    except Exception as e:
        logger.warning(f"[InviteBot] _db_save_event error: {e}")


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

    _schedule_bg(_db_save_link, entry)
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
        _schedule_bg(_db_save_link, entry)
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

    _schedule_bg(_db_save_link, entry)
    return entry


def mark_link_left(user_id: int, user_payload: dict[str, Any]) -> str | None:
    """Marks user as left and transitions their last link status to 'left'."""
    updated_at = user_payload.get("updated_at") or datetime.now(TIMEZONE).isoformat()
    invite_link = user_links.get(user_id) or user_payload.get("invite_link")
    if invite_link and invite_link in links_registry:
        links_registry[invite_link]["status"] = "left"
        links_registry[invite_link]["left_at"] = updated_at
        _schedule_bg(_db_save_link, links_registry[invite_link])

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
        _schedule_bg(_db_save_link, links_registry[invite_link])

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


async def fetch_link_status_async(invite_link: str) -> dict[str, Any] | None:
    """Returns link status from RAM cache, with fallback to MongoDB if server restarted."""
    cached = get_link_status(invite_link)
    if cached is not None:
        return cached

    if _is_db_ready():
        try:
            from src.database.models.invite_link import InviteLink
            doc = await InviteLink.find_one(InviteLink.invite_link == invite_link)
            if doc:
                entry = {
                    "invite_link": doc.invite_link,
                    "chat_id": doc.chat_id,
                    "status": doc.status,
                    "member_limit": doc.member_limit,
                    "created_at": doc.created_at.isoformat() if doc.created_at else None,
                    "expire_date": doc.expire_date,
                    "used_at": doc.used_at,
                    "used_by": doc.used_by,
                    "left_at": doc.left_at,
                    "banned_at": doc.banned_at,
                }
                links_registry[invite_link] = entry
                return entry
        except Exception as e:
            logger.warning(f"[InviteBot] fetch_link_status_async DB error: {e}")
    return None


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
    _schedule_bg(_db_save_event, payload)


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


async def reset_all_invite_data() -> None:
    """Clears both in-memory cache and MongoDB collections for test cleanup."""
    clear_events()
    if _is_db_ready():
        try:
            from src.database.models.invite_link import InviteLink
            from src.database.models.invite_event import InviteEvent
            await InviteLink.get_motor_collection().delete_many({})
            await InviteEvent.get_motor_collection().delete_many({})
            logger.info("[InviteBot] reset_all_invite_data: wiped DB invite collections")
        except Exception as e:
            logger.error(f"[InviteBot] reset_all_invite_data DB error: {e}")


__all__ = [
    "MAX_EVENTS",
    "MAX_LINKS",
    "MAX_USERS",
    "add_event",
    "build_avatar_url",
    "clear_events",
    "fetch_link_status_async",
    "get_events",
    "get_link_status",
    "get_user_status_entry",
    "links_registry",
    "mark_link_banned",
    "mark_link_left",
    "mark_link_used",
    "recent_events",
    "register_link",
    "reset_all_invite_data",
    "sign_file_id",
    "user_links",
    "user_statuses",
    "verify_file_id_signature",
]
