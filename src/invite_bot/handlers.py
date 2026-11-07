"""aiogram handlers of the invite bot: catches chat joins, leaves, and bans."""

from __future__ import annotations

from typing import Any

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberUpdated

from src.core.config import settings
from src.core.constants import TIMEZONE
from src.core.logging import get_logger
from src.invite_bot.events import (
    add_event,
    build_avatar_url,
    mark_link_banned,
    mark_link_left,
    mark_link_used,
)
from src.invite_bot.site_client import notify_site
from src.invite_bot.user_cache import record_user

logger = get_logger("invite_bot.handlers")

router = Router(name="invite_chat_member")


async def fetch_avatar_file_id(bot: Bot, user_id: int) -> str | None:
    """Returns file_id of the largest size of the user's current avatar, or None if hidden/absent."""
    try:
        photos = await bot.get_user_profile_photos(user_id=user_id, limit=1)
    except TelegramAPIError as e:
        logger.info(f"[InviteBot] avatar unavailable for tg_id={user_id}: {type(e).__name__}")
        return None
    if not photos or not photos.total_count or not photos.photos:
        return None
    sizes = photos.photos[0]
    return sizes[-1].file_id if sizes else None


def build_join_payload(event: ChatMemberUpdated, avatar_file_id: str | None) -> dict[str, Any]:
    user = event.new_chat_member.user
    link = event.invite_link
    return {
        "event": "joined",
        "status": "used",
        "telegram_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "avatar_file_id": avatar_file_id,
        "avatar_url": build_avatar_url(avatar_file_id),
        "chat_id": event.chat.id,
        "invite_link": link.invite_link if link else None,
        "joined_at": event.date.astimezone(TIMEZONE).isoformat(),
    }


def build_leave_payload(event: ChatMemberUpdated, last_link: str | None) -> dict[str, Any]:
    user = event.new_chat_member.user
    return {
        "event": "left",
        "status": "left",
        "telegram_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "chat_id": event.chat.id,
        "invite_link": last_link,
        "updated_at": event.date.astimezone(TIMEZONE).isoformat(),
    }


def build_ban_payload(event: ChatMemberUpdated, last_link: str | None) -> dict[str, Any]:
    user = event.new_chat_member.user
    return {
        "event": "banned",
        "status": "banned",
        "telegram_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "chat_id": event.chat.id,
        "invite_link": last_link,
        "updated_at": event.date.astimezone(TIMEZONE).isoformat(),
    }


@router.chat_member()
async def on_chat_member_updated(event: ChatMemberUpdated, bot: Bot) -> None:
    if event.chat.id not in settings.invite_chat_ids:
        return

    user = event.new_chat_member.user
    if user.is_bot:
        return

    record_user(user.id, user.username, user.first_name)

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    # 1. User Joined
    if old_status in ("left", "kicked") and new_status in ("member", "administrator", "restricted"):
        link = event.invite_link
        # Only links created by THIS bot: for foreign links Telegram hides part of the URL.
        if link is not None and link.creator is not None and link.creator.id == bot.id:
            avatar_file_id = await fetch_avatar_file_id(bot, user.id)
            payload = build_join_payload(event, avatar_file_id)
            add_event(payload)
            if link.invite_link:
                mark_link_used(link.invite_link, payload)
            logger.info(f"[InviteBot] join tg_id={user.id} chat={event.chat.id} link={link.invite_link}")
            await notify_site(payload)

    # 2. User Banned (Telegram ChatMemberBanned has status == 'kicked')
    elif new_status == "kicked":
        last_link = mark_link_banned(
            user.id,
            {"updated_at": event.date.astimezone(TIMEZONE).isoformat()},
        )
        payload = build_ban_payload(event, last_link)
        add_event(payload)
        logger.info(f"[InviteBot] ban tg_id={user.id} chat={event.chat.id}")
        await notify_site(payload)

    # 3. User Left (voluntarily or kicked)
    elif old_status in ("member", "administrator", "restricted") and new_status == "left":
        last_link = mark_link_left(
            user.id,
            {"updated_at": event.date.astimezone(TIMEZONE).isoformat()},
        )
        payload = build_leave_payload(event, last_link)
        add_event(payload)
        logger.info(f"[InviteBot] leave tg_id={user.id} chat={event.chat.id}")
        await notify_site(payload)


on_member_joined = on_chat_member_updated


__all__ = [
    "build_ban_payload",
    "build_join_payload",
    "build_leave_payload",
    "fetch_avatar_file_id",
    "on_chat_member_updated",
    "on_member_joined",
    "router",
]
