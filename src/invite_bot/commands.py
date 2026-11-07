"""Moderation and utility commands for @Karu_proxy_bot in groups and private chats.

Supports target resolution via:
1. Reply to message
2. @username mention (backed by in-memory user_cache)
3. Direct Telegram ID
4. Self (for /mute duration without target, or /id)

Isolated from the schedule bot (@kicho1bot).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import ChatPermissions, Message

from src.core.config import settings
from src.core.logging import get_logger
from src.invite_bot.events import register_link
from src.invite_bot.user_cache import (
    get_user_by_id,
    get_user_by_username,
    record_user,
)

logger = get_logger("invite_bot.commands")

router = Router(name="invite_commands")

SUPER_ADMIN_IDS = {1516988645}


def _record_message_users(message: Message) -> None:
    """Records users from messages and mentions to keep the username cache warm."""
    if message.from_user:
        record_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )
    if message.reply_to_message and message.reply_to_message.from_user:
        ru = message.reply_to_message.from_user
        record_user(ru.id, ru.username, ru.first_name)
    if message.entities:
        for ent in message.entities:
            if ent.type == "text_mention" and ent.user:
                record_user(ent.user.id, ent.user.username, ent.user.first_name)


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Checks if user is a chat administrator/creator or super admin."""
    if user_id in SUPER_ADMIN_IDS:
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


def parse_duration(arg: str) -> tuple[timedelta, str]:
    """Parses duration string like '15m', '2h', '1d'. Defaults to 1h."""
    arg = arg.strip().lower()
    if not arg:
        return timedelta(hours=1), "1ч"
    match = re.match(r"^(\d+)\s*([mмhчdд])?$", arg)
    if not match:
        return timedelta(hours=1), "1ч"
    val = int(match.group(1))
    unit = match.group(2) or "m"
    if unit in ("m", "м"):
        return timedelta(minutes=val), f"{val}м"
    if unit in ("h", "ч"):
        return timedelta(hours=val), f"{val}ч"
    if unit in ("d", "д"):
        return timedelta(days=val), f"{val}д"
    return timedelta(minutes=val), f"{val}м"


def get_user_mention(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    first = getattr(user, "first_name", "") or ""
    uid = getattr(user, "id", "")
    return first or f"ID:{uid}"


def resolve_target_and_args(
    message: Message,
    default_self: bool = False,
) -> tuple[int | None, str | None, bool, list[str], str | None]:
    """Resolves target user from reply, @username, or numeric ID.

    Returns:
        target_id: int | None
        target_mention: str | None
        is_self: bool
        remaining_tokens: list[str]
        error: str | None
    """
    _record_message_users(message)
    sender_id = message.from_user.id if message.from_user else 0
    raw_tokens = (message.text or "").split()[1:]

    # Case 1: Reply to a message
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        is_self = (target.id == sender_id)
        return target.id, get_user_mention(target), is_self, raw_tokens, None

    # Case 2: Telegram text_mention entity
    if message.entities:
        for ent in message.entities:
            if ent.type == "text_mention" and ent.user:
                target = ent.user
                is_self = (target.id == sender_id)
                return target.id, get_user_mention(target), is_self, raw_tokens, None

    # Case 3: Token with @username
    mention_token = next((t for t in raw_tokens if t.startswith("@")), None)
    if mention_token:
        clean = mention_token.lstrip("@").strip()
        cached = get_user_by_username(clean)
        if cached:
            target_id = cached["user_id"]
            target_mention = f"@{cached.get('username') or clean}"
            is_self = (target_id == sender_id)
            remaining = [t for t in raw_tokens if t != mention_token]
            return target_id, target_mention, is_self, remaining, None
        return (
            None,
            None,
            False,
            raw_tokens,
            f"⚠️ Пользователя @{clean} пока нет в памяти бота (он ещё ничего не писал при мне). Ответь на любое его сообщение в чате.",
        )

    # Case 4: Token with numeric user ID
    id_token = next((t for t in raw_tokens if t.isdigit()), None)
    if id_token:
        # If this is /mute with a single small number (<= 1440), treat as self-mute duration in minutes
        if default_self and len(raw_tokens) == 1 and int(id_token) <= 1440:
            if message.from_user:
                return message.from_user.id, get_user_mention(message.from_user), True, raw_tokens, None
        target_id = int(id_token)
        cached = get_user_by_id(target_id)
        target_mention = f"@{cached['username']}" if (cached and cached.get("username")) else f"ID:<code>{target_id}</code>"
        is_self = (target_id == sender_id)
        remaining = [t for t in raw_tokens if t != id_token]
        return target_id, target_mention, is_self, remaining, None

    # Case 5: Default to self (used by /mute for self-mute, and /id)
    if default_self and message.from_user:
        target = message.from_user
        return target.id, get_user_mention(target), True, raw_tokens, None

    return None, None, False, raw_tokens, None


@router.message(Command("help", "ahelp"))
async def cmd_help(message: Message, bot: Bot) -> None:
    _record_message_users(message)
    user_id = message.from_user.id if message.from_user else 0
    is_adm = True
    if message.chat.type != "private":
        is_adm = await is_admin(bot, message.chat.id, user_id)

    if not is_adm:
        text = (
            "⚡️ <b>Команды:</b>\n\n"
            "/id\n"
            "/mute 30m"
        )
    else:
        text = (
            "🛠 <b>Команды админа:</b>\n\n"
            "/mute @user 15m\n"
            "/unmute @user\n"
            "/kick @user\n"
            "/ban @user\n"
            "/unban ID\n"
            "/id @user\n"
            "/link\n"
            "/mute 30m"
        )
    await message.reply(text, parse_mode="HTML")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    _record_message_users(message)

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        mention = get_user_mention(target)
        await message.reply(f"🆔 ID {mention}: <code>{target.id}</code>", parse_mode="HTML")
        return

    tokens = (message.text or "").split()[1:]
    mention_token = next((t for t in tokens if t.startswith("@")), None)
    if mention_token:
        clean = mention_token.lstrip("@").strip()
        cached = get_user_by_username(clean)
        if cached:
            await message.reply(
                f"🆔 ID @{cached.get('username') or clean}: <code>{cached['user_id']}</code>",
                parse_mode="HTML",
            )
        else:
            await message.reply(
                f"⚠️ Пользователя @{clean} пока нет в памяти бота (он ещё ничего не писал при мне).",
                parse_mode="HTML",
            )
        return

    user = message.from_user
    if not user:
        return
    if message.chat.type == "private":
        await message.reply(f"🆔 Твой ID: <code>{user.id}</code>", parse_mode="HTML")
    else:
        await message.reply(
            f"🆔 Твой ID: <code>{user.id}</code> | Чат: <code>{message.chat.id}</code>",
            parse_mode="HTML",
        )


@router.message(Command("link"))
async def cmd_link(message: Message, bot: Bot) -> None:
    _record_message_users(message)
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    target_chat_id = settings.invite_chat_ids[0] if settings.invite_chat_ids else chat_id

    if message.chat.type != "private":
        target_chat_id = chat_id
        if not await is_admin(bot, chat_id, user_id):
            await message.reply("⛔️ Не дорос, команда только для админов")
            return
    elif user_id not in SUPER_ADMIN_IDS:
        await message.reply("⛔️ Команда доступна только администраторам")
        return

    try:
        link = await bot.create_chat_invite_link(
            chat_id=target_chat_id,
            member_limit=1,
            expire_date=None,
        )
        register_link(link.invite_link, chat_id=target_chat_id)
        await message.reply(f"🔗 Ссылка на 1 вход: {link.invite_link}")
    except Exception as e:
        logger.error(f"[InviteBot] /link failed: {e}")
        await message.reply(f"❌ Ошибка создания ссылки: {e}")


@router.message(Command("mute"))
async def cmd_mute(message: Message, bot: Bot) -> None:
    if message.chat.type == "private":
        await message.reply("Мут работает только в группах")
        return

    sender = message.from_user
    if not sender:
        return

    target_id, mention, is_self, remaining, err = resolve_target_and_args(message, default_self=True)
    if err:
        await message.reply(err)
        return

    if not target_id:
        return

    if not is_self:
        if not await is_admin(bot, message.chat.id, sender.id):
            await message.reply("⛔️ Не дорос, команда только для админов")
            return

    duration_arg = " ".join(remaining)
    duration, duration_str = parse_duration(duration_arg)
    until = datetime.now(timezone.utc) + duration

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        sender_mention = get_user_mention(sender)
        target_text = "себя" if is_self else mention
        await message.reply(f"🤐 {sender_mention} замутил {target_text} на {duration_str}")
    except TelegramBadRequest as e:
        err_str = str(e).lower()
        if "can't restrict" in err_str or "not enough rights" in err_str or "administrator" in err_str:
            if is_self:
                await message.reply(
                    f"👑 Ты создатель чата, Telegram запрещает ботам тебя затыкать. Но мы делаем вид, что ты в муте на {duration_str}, держи слово! 🤫"
                )
            else:
                await message.reply("⛔️ У меня нет прав замутить этого пользователя")
        else:
            await message.reply(f"❌ Ошибка мута: {e}")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, bot: Bot) -> None:
    if message.chat.type == "private":
        return
    sender_id = message.from_user.id if message.from_user else 0
    if not await is_admin(bot, message.chat.id, sender_id):
        await message.reply("⛔️ Не дорос, команда только для админов")
        return

    target_id, mention, is_self, remaining, err = resolve_target_and_args(message, default_self=False)
    if err:
        await message.reply(err)
        return

    if not target_id:
        await message.reply("Укажи пользователя: /unmute @username или ответь на сообщение")
        return

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        await message.reply(f"🔊 {mention} можешь говорить, мут снят")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


@router.message(Command("kick"))
async def cmd_kick(message: Message, bot: Bot) -> None:
    if message.chat.type == "private":
        return
    sender_id = message.from_user.id if message.from_user else 0
    if not await is_admin(bot, message.chat.id, sender_id):
        await message.reply("⛔️ Не дорос, команда только для админов")
        return

    target_id, mention, is_self, remaining, err = resolve_target_and_args(message, default_self=False)
    if err:
        await message.reply(err)
        return

    if not target_id:
        await message.reply("Укажи пользователя: /kick @username или ответь на сообщение")
        return

    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id)
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
        await message.reply(f"🚪 {mention} вылетел из чата")
    except Exception as e:
        await message.reply(f"❌ Не могу кикнуть: {e}")


@router.message(Command("ban"))
async def cmd_ban(message: Message, bot: Bot) -> None:
    if message.chat.type == "private":
        return
    sender_id = message.from_user.id if message.from_user else 0
    if not await is_admin(bot, message.chat.id, sender_id):
        await message.reply("⛔️ Не дорос, команда только для админов")
        return

    target_id, mention, is_self, remaining, err = resolve_target_and_args(message, default_self=False)
    if err:
        await message.reply(err)
        return

    if not target_id:
        await message.reply("Укажи пользователя: /ban @username или ответь на сообщение")
        return

    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target_id)
        await message.reply(f"🚫 {mention} улетел в бан навсегда")
    except Exception as e:
        await message.reply(f"❌ Не могу забанить: {e}")


@router.message(Command("unban"))
async def cmd_unban(message: Message, bot: Bot) -> None:
    if message.chat.type == "private":
        return
    sender_id = message.from_user.id if message.from_user else 0
    if not await is_admin(bot, message.chat.id, sender_id):
        await message.reply("⛔️ Не дорос, команда только для админов")
        return

    target_id, mention, is_self, remaining, err = resolve_target_and_args(message, default_self=False)
    if err:
        await message.reply(err)
        return

    if not target_id:
        await message.reply("Укажи ID или @username: /unban 12345678 или ответь на сообщение")
        return

    try:
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
        await message.reply(f"✅ Пользователь {mention} разбанен")
    except Exception as e:
        await message.reply(f"❌ Ошибка разбана: {e}")


@router.message()
async def on_chat_message(message: Message) -> None:
    """Passive listener for all non-command messages to keep the username cache warm."""
    _record_message_users(message)


__all__ = [
    "cmd_ban",
    "cmd_help",
    "cmd_id",
    "cmd_kick",
    "cmd_link",
    "cmd_mute",
    "cmd_unban",
    "cmd_unmute",
    "is_admin",
    "on_chat_message",
    "parse_duration",
    "resolve_target_and_args",
    "router",
]
