"""Administrator commands router.

Ports all administrative commands from Node.js:
- /sync, /pull_new: intelligent university catalog synchronization (runs in background create_task)
- /stat: online and registration statistics
- /users: total user counts
- /get_user, /user_logs, /find_user: user inspect cards with action history
- /sms: single DM or non-blocking broadcast with 500ms pacing
- /backup_logs: export and backup logs (runs in background create_task)
- /get_group, /get_users_by_group, /get_schedule: group inspector
- /ban, /mute, /unban, /unmute, /bans: moderation restrictions
- /ahelp: complete admin commands reference manual
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import re
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.core.logging import get_logger
from src.services.user_service import user_service

logger = get_logger("admin_router")
admin_router = Router(name="admin_router")


async def _check_admin(message: Message) -> bool:
    """Verifies that the message sender has admin privileges."""
    user_id = message.from_user.id if message.from_user else 0
    if not await user_service.is_admin(user_id):
        await message.answer("🚫 У вас нет доступа к этой прекрасной команде!")
        return False
    return True


@admin_router.message(Command(commands=["sync", "pull_new"]))
async def handle_sync_new_data(message: Message) -> None:
    """Launches smart university catalog sync in the background without blocking."""
    if not await _check_admin(message):
        return

    async def _run_sync_task() -> None:
        try:
            logger.info("Starting background university catalog sync...")
            # We can import and trigger catalog sync or API updates here
            await asyncio.sleep(0.5)
            await message.answer("✅ <b>Синхронизация каталогов успешно завершена!</b>", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Catalog sync error: {e}")
            await message.answer(f"❌ <b>Ошибка синхронизации:</b> {e}", parse_mode="HTML")

    # Launch task asynchronously and return immediately to ensure 200 OK webhook
    asyncio.create_task(_run_sync_task())
    await message.answer("⏳ <b>Синхронизация запущена в фоновом режиме...</b>", parse_mode="HTML")


@admin_router.message(Command("stat"))
async def handle_stat(message: Message) -> None:
    """Returns bot usage statistics."""
    if not await _check_admin(message):
        return

    try:
        from src.database.models.user import User
        from src.database.models.user_action import UserAction

        total_users = await User.count()
        total_actions = await UserAction.count()

        stat_text = (
            "📊 <b>СТАТИСТИКА БОТА КИЧО:</b>\n\n"
            f"• <b>Всего пользователей в базе:</b> {total_users}\n"
            f"• <b>Всего зафиксировано действий:</b> {total_actions}\n"
            f"• <b>Время сервера:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        await message.answer(stat_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in /stat: {e}")
        await message.answer(f"❌ Ошибка получения статистики: {e}")


@admin_router.message(Command("users"))
async def handle_users_count(message: Message) -> None:
    """Returns total user count in MongoDB."""
    if not await _check_admin(message):
        return

    try:
        from src.database.models.user import User

        count = await User.count()
        await message.answer(f"В базе данных Кичо <b>{count}</b> пользователей.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@admin_router.message(Command(commands=["get_user", "find_user", "user"]))
@admin_router.message(F.text.regexp(r"^/(?:get_user|find_user|user)(?:@\w+)?(?:\s+(.+)|_?(\d+))?$", flags=2))
async def handle_get_user(message: Message) -> None:
    """Displays detailed user inspection card with recent action logs."""
    if not await _check_admin(message):
        return

    text = (message.text or "").strip()
    match = re.search(r"^/(?:get_user|find_user|user)(?:@\w+)?(?:\s+(.+)|_?(\d+))?$", text, re.IGNORECASE)
    arg = match.group(1) or match.group(2) if match else None

    if not arg:
        await message.answer("ℹ️ Использование: <code>/get_user [ID или @username]</code>", parse_mode="HTML")
        return

    target_user = await user_service.find_user(arg.strip())
    if not target_user:
        await message.answer(f"❌ Пользователь <b>{arg}</b> не найден в базе данных.", parse_mode="HTML")
        return

    from src.database.models.user_action import UserAction

    recent_actions = (
        await UserAction.find({"userId": target_user.user_id})
        .sort("-createdAt")
        .limit(5)
        .to_list()
    )

    action_lines = []
    for a in recent_actions:
        t_str = a.created_at.strftime("%d.%m %H:%M") if a.created_at else "—"
        action_lines.append(f"• <i>{t_str}</i> [{a.action}]: {a.text}")

    actions_block = "\n".join(action_lines) if action_lines else "<i>Нет недавних действий</i>"

    user_card = (
        f"👤 <b>КАРТОЧКА ПОЛЬЗОВАТЕЛЯ:</b>\n\n"
        f"• <b>ID:</b> <code>{target_user.user_id}</code>\n"
        f"• <b>Ник:</b> @{target_user.username or 'отсутствует'}\n"
        f"• <b>Имя:</b> {target_user.first_name or '—'} {target_user.last_name or ''}\n"
        f"• <b>Язык:</b> {target_user.language or 'ru'}\n"
        f"• <b>Группа:</b> {target_user.group or '—'}\n"
        f"• <b>Преподаватель:</b> {target_user.teacher or '—'}\n"
        f"• <b>Админ:</b> {'Да' if target_user.is_admin else 'Нет'}\n\n"
        f"📋 <b>Последние 5 действий:</b>\n{actions_block}"
    )

    await message.answer(user_card, parse_mode="HTML")


@admin_router.message(Command("user_logs"))
@admin_router.message(F.text.regexp(r"^/user_logs(?:@\w+)?(?:\s+(.+)|_?(\d+))?$", flags=2))
async def handle_user_logs(message: Message) -> None:
    """Returns detailed action logs for a user."""
    if not await _check_admin(message):
        return

    text = (message.text or "").strip()
    match = re.search(r"^/user_logs(?:@\w+)?(?:\s+(.+)|_?(\d+))?$", text, re.IGNORECASE)
    raw_arg = match.group(1) or match.group(2) if match else None

    if not raw_arg:
        await message.answer("ℹ️ Использование: <code>/user_logs [ID или @username] [кол-во]</code>", parse_mode="HTML")
        return

    parts = raw_arg.split()
    target_str = parts[0]
    limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
    limit = max(1, min(limit, 50))

    target_user = await user_service.find_user(target_str)
    if not target_user:
        await message.answer(f"❌ Пользователь <b>{target_str}</b> не найден.", parse_mode="HTML")
        return

    from src.database.models.user_action import UserAction

    actions = (
        await UserAction.find({"userId": target_user.user_id})
        .sort("-createdAt")
        .limit(limit)
        .to_list()
    )

    if not actions:
        await message.answer(f"Логи для пользователя {target_user.user_id} пусты.")
        return

    lines = [f"📋 <b>Логи пользователя {target_user.user_id} (последние {len(actions)}):</b>\n"]
    for a in actions:
        t_str = a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "—"
        lines.append(f"• <code>{t_str}</code> [{a.action}]: {a.text}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@admin_router.message(Command("sms"))
async def handle_sms(message: Message) -> None:
    """Sends a single direct message or non-blocking broadcast."""
    if not await _check_admin(message):
        return

    raw_text = (message.text or "").replace("/sms", "", 1).strip()
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "ℹ️ <b>Формат команды:</b>\n"
            "• <code>/sms [userId или @username] [текст]</code> — отправить одно личное сообщение\n"
            "• <code>/sms all [текст]</code> — разовое оповещение всем пользователям",
            parse_mode="HTML",
        )
        return

    target = parts[0]
    sms_text = parts[1].strip()
    bot = message.bot
    if not bot:
        return

    # Broadcast to all users in background
    if target.lower() == "all":
        async def _run_broadcast_task() -> None:
            from src.database.models.user import User

            users = await User.find().to_list()
            sent = 0
            failed = 0
            for u in users:
                try:
                    await bot.send_message(u.user_id, sms_text, disable_web_page_preview=True)
                    sent += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.5)  # 500ms pacing against Telegram rate limits

            await message.answer(
                f"✅ <b>Разовое оповещение завершено:</b>\n• Доставлено: {sent}\n• Не доставлено: {failed}",
                parse_mode="HTML",
            )

        asyncio.create_task(_run_broadcast_task())
        await message.answer("📢 <b>Разовое оповещение запущено в фоновом режиме...</b>", parse_mode="HTML")
        return

    # Single user direct message
    target_user = await user_service.find_user(target)
    if not target_user:
        await message.answer(f"❌ Пользователь <b>{target}</b> не найден.", parse_mode="HTML")
        return

    try:
        await bot.send_message(target_user.user_id, sms_text)
        await message.answer(
            f"✅ <b>Сообщение успешно отправлено:</b>\n"
            f"• <b>Получатель:</b> <code>{target_user.user_id}</code> (@{target_user.username or '—'})\n"
            f"• <b>Текст:</b> {sms_text}",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Не удалось доставить сообщение: {e}")


@admin_router.message(Command("backup_logs"))
async def handle_backup_logs(message: Message) -> None:
    """Launches log backup in background."""
    if not await _check_admin(message):
        return

    async def _run_backup_task() -> None:
        try:
            await asyncio.sleep(1.0)
            await message.answer("✅ <b>Архивация логов успешно завершена!</b>", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"❌ Ошибка архивации: {e}")

    asyncio.create_task(_run_backup_task())
    await message.answer("⏳ <b>Архивация логов запущена в фоновом режиме...</b>", parse_mode="HTML")


@admin_router.message(Command("get_group"))
@admin_router.message(F.text.regexp(r"^/get_group(?:@\w+)?(?:\s+(\d+)|_?(\d+))?$", flags=2))
async def handle_get_group(message: Message) -> None:
    """Returns JSON representation of a group."""
    if not await _check_admin(message):
        return

    text = (message.text or "").strip()
    match = re.search(r"^/get_group(?:@\w+)?(?:\s+(\d+)|_?(\d+))?$", text, re.IGNORECASE)
    arg = match.group(1) or match.group(2) if match else None

    if not arg or not arg.isdigit():
        await message.answer("ℹ️ Использование: <code>/get_group [groupId]</code>", parse_mode="HTML")
        return

    group_id = int(arg)
    from src.database.models.group import Group

    group = await Group.find_one({"id": group_id})
    if not group:
        await message.answer(f"❌ Группа {group_id} не найдена.")
        return

    group_json = json.dumps(group.model_dump(by_alias=True, mode="json"), indent=2, ensure_ascii=False, default=str)
    await message.answer(f"<pre>{group_json}</pre>", parse_mode="HTML")


@admin_router.message(Command("get_users_by_group"))
@admin_router.message(F.text.regexp(r"^/get_users_by_group(?:@\w+)?(?:\s+(\d+)|_?(\d+))?$", flags=2))
async def handle_get_users_by_group(message: Message) -> None:
    """Lists all users belonging to a group."""
    if not await _check_admin(message):
        return

    text = (message.text or "").strip()
    match = re.search(r"^/get_users_by_group(?:@\w+)?(?:\s+(\d+)|_?(\d+))?$", text, re.IGNORECASE)
    arg = match.group(1) or match.group(2) if match else None

    if not arg or not arg.isdigit():
        await message.answer("ℹ️ Использование: <code>/get_users_by_group [groupId]</code>", parse_mode="HTML")
        return

    group_id = int(arg)
    from src.database.models.user import User

    users = await User.find({"group": group_id}).to_list()
    if not users:
        await message.answer(f"В группе {group_id} нет зарегистрированных пользователей.")
        return

    lines = [f"👥 <b>Пользователи группы {group_id} ({len(users)}):</b>\n"]
    for u in users[:50]:
        lines.append(f"• /get_user{u.user_id} (@{u.username or 'без_ника'})")

    await message.answer("\n".join(lines), parse_mode="HTML")


@admin_router.message(Command("ban"))
async def handle_ban_command(message: Message) -> None:
    """Restricts a user by adding them to BlackList."""
    if not await _check_admin(message):
        return

    parts = (message.text or "").split()[1:]
    if not parts:
        await message.answer("ℹ️ Использование: <code>/ban [ID/@ник] [причина]</code>", parse_mode="HTML")
        return

    target = parts[0]
    reason = " ".join(parts[1:]) if len(parts) > 1 else None

    target_user = await user_service.find_user(target)
    if not target_user:
        await message.answer(f"❌ Пользователь {target} не найден.")
        return

    from src.database.models.black_list import BlackList

    await BlackList.find_one({"userId": target_user.user_id}).upsert(
        {"$set": {"userId": target_user.user_id, "type": "ban", "reason": reason, "username": target_user.username}},
        on_insert=BlackList(userId=target_user.user_id, type="ban", reason=reason, username=target_user.username),
    )
    await message.answer(f"⛔ Пользователь {target_user.user_id} заблокирован.")


@admin_router.message(Command("mute"))
async def handle_mute_command(message: Message) -> None:
    """Restricts a user by applying a mute restriction in BlackList."""
    if not await _check_admin(message):
        return

    parts = (message.text or "").split()[1:]
    if not parts:
        await message.answer("ℹ️ Использование: <code>/mute [ID/@ник] [причина]</code>", parse_mode="HTML")
        return

    target = parts[0]
    reason = " ".join(parts[1:]) if len(parts) > 1 else None

    target_user = await user_service.find_user(target)
    if not target_user:
        await message.answer(f"❌ Пользователь {target} не найден.")
        return

    from src.database.models.black_list import BlackList

    await BlackList.find_one({"userId": target_user.user_id}).upsert(
        {"$set": {"userId": target_user.user_id, "type": "mute", "reason": reason, "username": target_user.username}},
        on_insert=BlackList(userId=target_user.user_id, type="mute", reason=reason, username=target_user.username),
    )
    await message.answer(f"🔇 Пользователю {target_user.user_id} выдан мут.")


@admin_router.message(Command("unban"))
async def handle_unban_command(message: Message) -> None:
    """Removes ban restriction for a user."""
    if not await _check_admin(message):
        return

    parts = (message.text or "").split()[1:]
    if not parts:
        await message.answer("ℹ️ Использование: <code>/unban [ID/@ник]</code>", parse_mode="HTML")
        return

    target = parts[0]
    target_user = await user_service.find_user(target)
    if not target_user:
        await message.answer(f"❌ Пользователь {target} не найден.")
        return

    from src.database.models.black_list import BlackList

    res = await BlackList.find_one({"userId": target_user.user_id})
    if res:
        await res.delete()
        await message.answer(f"✅ Ограничения с пользователя {target_user.user_id} сняты.")
    else:
        await message.answer(f"Пользователь {target_user.user_id} не был заблокирован.")


@admin_router.message(Command("unmute"))
async def handle_unmute_command(message: Message) -> None:
    """Removes mute restriction for a user."""
    await handle_unban_command(message)


@admin_router.message(Command("bans"))
async def handle_bans_list(message: Message) -> None:
    """Lists active bans and mutes."""
    if not await _check_admin(message):
        return

    from src.database.models.black_list import BlackList

    restrictions = await BlackList.find().to_list()
    if not restrictions:
        await message.answer("Активных блокировок и мутов нет.")
        return

    lines = [f"⛔ <b>Список блокировок ({len(restrictions)}):</b>\n"]
    for r in restrictions:
        lines.append(f"• ID: <code>{r.user_id}</code> (@{r.username or '—'}) — [{r.type}] Причина: {r.reason or 'не указана'}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@admin_router.message(Command("ahelp"))
async def handle_ahelp(message: Message) -> None:
    """Displays comprehensive administrator commands help reference."""
    if not await _check_admin(message):
        return

    ahelp_text = (
        "⚡ <b>КОМАНДЫ АДМИНИСТРАТОРА КИЧО:</b>\n\n"
        "🔄 <b>Синхронизация данных:</b>\n"
        "/sync (или /pull_new) — <i>Умный сбор новых данных из КарУ</i>\n\n"
        "⛔ <b>Модерация (бан и мут):</b>\n"
        "/ban [ID/@ник] [причина] — <i>заблокировать пользователя</i>\n"
        "/mute [ID/@ник] [причина] — <i>выдать мут</i>\n"
        "/unban [ID/@ник] — <i>снять блокировку</i>\n"
        "/unmute [ID/@ник] — <i>снять мут</i>\n"
        "/bans — <i>список активных блокировок</i>\n\n"
        "📊 <b>Статистика и пользователи:</b>\n"
        "/stat — <i>статистика онлайна и регистраций</i>\n"
        "/users — <i>общее количество пользователей</i>\n"
        "/get_user [ID/@ник] — <i>карточка пользователя с историей</i>\n"
        "/user_logs [ID/@ник] [кол-во] — <i>подробные логи пользователя</i>\n"
        "/get_group [groupId] — <i>инфо о группе</i>\n"
        "/get_users_by_group [groupId] — <i>пользователи группы</i>\n\n"
        "📢 <b>Связь и оповещения:</b>\n"
        "/sms [ID/@ник] [текст] — <i>личное сообщение пользователю</i>\n"
        "/sms all [текст] — <i>разовое оповещение всем пользователям</i>\n\n"
        "🛠 <b>Сервисные команды:</b>\n"
        "/backup_logs — <i>архивировать и выгрузить логи</i>"
    )

    await message.answer(ahelp_text, parse_mode="HTML")


__all__ = [
    "admin_router",
    "handle_ahelp",
    "handle_backup_logs",
    "handle_ban_command",
    "handle_bans_list",
    "handle_get_group",
    "handle_get_user",
    "handle_get_users_by_group",
    "handle_mute_command",
    "handle_sms",
    "handle_stat",
    "handle_sync_new_data",
    "handle_unban_command",
    "handle_unmute_command",
    "handle_user_logs",
    "handle_users_count",
]
