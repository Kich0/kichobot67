"""Invite bot (@Karu_proxy_bot) for the partner site.

One-time Telegram invite links + join notifications. Runs inside the main
FastAPI server but is isolated from the schedule bot (own Bot/Dispatcher).
Disabled unless INVITE_BOT_TOKEN, INVITE_CHAT_IDS and INVITE_API_KEY are set.
"""

from src.invite_bot.api import drain_invite_tasks, router as invite_api_router
from src.invite_bot.instance import (
    INVITE_ALLOWED_UPDATES,
    close_invite_bot,
    get_invite_bot,
    get_invite_dispatcher,
)

__all__ = [
    "INVITE_ALLOWED_UPDATES",
    "close_invite_bot",
    "drain_invite_tasks",
    "get_invite_bot",
    "get_invite_dispatcher",
    "invite_api_router",
]
