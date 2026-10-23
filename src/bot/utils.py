"""Bot utility functions for message inspection and routing.

Provides helpers to determine if messages in group chats are addressed to the bot
or represent casual chatter between group members.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.types import Message

# Matches schedule summon phrases: 'расписание', 'кесте', 'кестесі' with word boundary or whitespace
SCHEDULE_TRIGGERS_REGEX = re.compile(
    r"^(?:расписание|кесте|кестесі)(?:\b|\s|$)",
    re.IGNORECASE,
)


def is_bot_mentioned(message: Message, bot_username: str | None = None) -> tuple[bool, str]:
    """Checks if the bot is mentioned in the message text or entities.

    Returns:
        tuple[bool, str]: (is_mentioned, clean_text_without_bot_mention)
    """
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not text:
        return False, ""

    from src.core.config import settings

    expected = (
        bot_username
        or getattr(settings, "BOT_USERNAME", "kicho1bot")
        or "kicho1bot"
    ).lower().lstrip("@")

    # 1. Inspect Message Entities for explicit mentions
    entities = getattr(message, "entities", None) or getattr(message, "caption_entities", None) or []
    for ent in entities:
        if ent.type == "mention":
            mention_str = text[ent.offset : ent.offset + ent.length].lower().lstrip("@")
            if mention_str == expected or mention_str.endswith("bot"):
                clean = (text[: ent.offset] + text[ent.offset + ent.length :]).strip()
                return True, clean
        elif ent.type == "text_mention" and ent.user:
            bot_id = getattr(settings, "BOT_ID", 0)
            if ent.user.is_bot and (not bot_id or ent.user.id == bot_id):
                clean = (text[: ent.offset] + text[ent.offset + ent.length :]).strip()
                return True, clean

    # 2. Check plain text prefix '@<username>'
    mention_pattern = re.compile(
        rf"^@({re.escape(expected)}|\w+bot)(?:\s+(.*))?$",
        re.IGNORECASE,
    )
    m = mention_pattern.match(text)
    if m:
        clean = (m.group(2) or "").strip()
        return True, clean

    # Check if @<username> is at start of string with word boundary
    prefix_pattern = re.compile(
        rf"^@({re.escape(expected)}|\w+bot)\b\s*",
        re.IGNORECASE,
    )
    if prefix_pattern.search(text):
        clean = prefix_pattern.sub("", text).strip()
        return True, clean

    return False, text


def is_message_for_bot(message: Message, bot_username: str | None = None) -> bool:
    """Determines whether a message in any chat type is intended for the bot.

    Rules:
    - In private chats: Always True (all messages are directed to the bot).
    - In group / supergroup / channel chats:
        - True if message starts with '/' (bot command).
        - True if message invokes schedule ('расписание', 'кесте', 'кестесі').
        - True if bot is mentioned (@kicho1bot or similar).
        - False for casual chatter between group members.
    """
    chat = getattr(message, "chat", None)
    if not chat or chat.type == "private":
        return True

    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not text:
        return False

    # 1. Commands starting with '/'
    if text.startswith("/"):
        return True

    # 2. Schedule summoning keywords ('расписание', 'кесте', 'кестесі')
    if SCHEDULE_TRIGGERS_REGEX.match(text):
        return True

    # 3. Explicit bot mention
    is_mentioned, _ = is_bot_mentioned(message, bot_username)
    if is_mentioned:
        return True

    return False


__all__ = [
    "SCHEDULE_TRIGGERS_REGEX",
    "is_bot_mentioned",
    "is_message_for_bot",
]
