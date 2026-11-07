"""aiogram handlers of the invite bot: catches chat joins via links created by this bot."""

from __future__ import annotations

from typing import Any

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import JOIN_TRANSITION, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated

from src.core.config import settings
from src.core.constants import TIMEZONE
from src.core.logging import get_logger
from src.invite_bot.events import add_event, build_avatar_url, mark_link_used
from src.invite_bot.site_client import notify_site
from src.invite_bot.user_cache import record_user

logger = get_logger("invite_bot.handlers")

router = Router(name="invite_join")


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
        "invite_link": link.invite_link if link else None,
        "status": "used",
        "telegram_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "avatar_file_id": avatar_file_id,
        "avatar_url": build_avatar_url(avatar_file_id),
        "chat_id": event.chat.id,
        "joined_at": event.date.astimezone(TIMEZONE).isoformat(),
    }


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_member_joined(event: ChatMemberUpdated, bot: Bot) -> None:
    if event.chat.id not in settings.invite_chat_ids:
        return

    link = event.invite_link
    # Only links created by THIS bot: for foreign links Telegram hides part of the URL.
    if link is None or link.creator is None or link.creator.id != bot.id:
        return

    user = event.new_chat_member.user
    if user.is_bot:
        return

    record_user(user.id, user.username, user.first_name)

    avatar_file_id = await fetch_avatar_file_id(bot, user.id)
    payload = build_join_payload(event, avatar_file_id)
    add_event(payload)
    if link.invite_link:
        mark_link_used(link.invite_link, payload)
    logger.info(f"[InviteBot] join tg_id={user.id} chat={event.chat.id} link={link.invite_link}")

    await notify_site(payload)


__all__ = ["router", "on_member_joined", "build_join_payload", "fetch_avatar_file_id"]
