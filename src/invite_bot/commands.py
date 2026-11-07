"""Moderation and utility commands for @Karu_proxy_bot in groups and private chats.

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

logger = get_logger("invite_bot.commands")

router = Router(name="invite_commands")

SUPER_ADMIN_IDS = {1516988645}


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
    if user.username:
        return f"@{user.username}"
    return user.first_name or f"ID:{user.id}"


@router.message(Command("help", "ahelp"))
async def cmd_help(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id if message.from_user else 0
    is_adm = True
    if message.chat.type != "private":
        is_adm = await is_admin(bot, message.chat.id, user_id)

    if not is_adm:
        text = (
            "⚡️ <b>Команды:</b>\n\n"
            "• <code>/id</code> — узнать свой Telegram ID\n"
            "• <code>/mute 30m</code> — самомут, чтобы не отвлекаться на чат"
        )
    else:
        text = (
            "🛠 <b>Команды админа (ответом на смс):</b>\n\n"
            "• <code>/mute 15m</code> — мут на время (m / h / d)\n"
            "• <code>/unmute</code> — снять мут\n"
            "• <code>/kick</code> — выкинуть из чата\n"
            "• <code>/ban</code> — бан навсегда\n"
            "• <code>/unban &lt;ID&gt;</code> — снять бан\n\n"
            "⚡️ <b>Быстрые:</b>\n"
            "• <code>/id</code> — чекнуть ID (свой или чужой)\n"
            "• <code>/link</code> — сделать ссылку на 1 вход"
        )
    await message.reply(text, parse_mode="HTML")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        mention = get_user_mention(target)
        await message.reply(f"🆔 ID {mention}: <code>{target.id}</code>", parse_mode="HTML")
    else:
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

    is_self = False
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if target.id == sender.id:
            is_self = True
    else:
        target = sender
        is_self = True

    if not is_self:
        if not await is_admin(bot, message.chat.id, sender.id):
            await message.reply("⛔️ Не дорос, команда только для админов")
            return

    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1] if len(parts) > 1 else ""
    duration, duration_str = parse_duration(arg)

    until = datetime.now(timezone.utc) + duration
    mention = get_user_mention(target)

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        if is_self:
            await message.reply(f"🤐 {mention} в добровольном муте на {duration_str}, удачи с делами")
        else:
            await message.reply(f"🤐 {mention} в муте на {duration_str}, подумай о поведении")
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "can't restrict" in err or "not enough rights" in err or "administrator" in err:
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

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Ответь командой /unmute на сообщение пользователя")
        return

    target = message.reply_to_message.from_user
    mention = get_user_mention(target)

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
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

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Ответь командой /kick на сообщение пользователя")
        return

    target = message.reply_to_message.from_user
    mention = get_user_mention(target)

    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id)
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=target.id)
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

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Ответь командой /ban на сообщение пользователя")
        return

    target = message.reply_to_message.from_user
    mention = get_user_mention(target)

    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id)
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

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        parts = (message.text or "").split()
        if len(parts) > 1 and parts[1].isdigit():
            target_id = int(parts[1])

    if not target_id:
        await message.reply("Укажи ID: /unban 12345678 или ответь на сообщение")
        return

    try:
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
        await message.reply(f"✅ Пользователь {target_id} разбанен")
    except Exception as e:
        await message.reply(f"❌ Ошибка разбана: {e}")
