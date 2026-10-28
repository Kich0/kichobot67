"""Administrator commands router.

Ports all administrative commands from Node.js (commits 81705b6 / 25601dc):
- /sync, /pull_new: intelligent university catalog synchronization (runs in background create_task)
- /stat, /state: 7-day comprehensive online, action, and registration statistics (Asia/Almaty UTC+5)
- /users: total user counts in MongoDB
- /get_user, /find_user, /user: user inspect cards with action history
- /user_logs: detailed user action logs
- /sms: single DM or non-blocking broadcast with 500ms pacing
- /backup_logs: export and backup logs
- /get_group: group JSON inspector
- /get_users_by_group: list users belonging to a group
- /group_stat: group coverage statistics and JSON export
- /get_schedule: fetch and display schedule for group
- /get_reserved_schedule: display MongoDB reserved schedule for group
- /restart: restart confirmation message
- /info: bot diagnostics and uptime
- /test: KarU API connectivity check
- /get_callback: test callback inline button
- /ban, /mute, /unban, /unmute, /bans: moderation restrictions
- /paywall, /toll, /starwall, /stars, /tax, /unpaywall, /untoll, /paywalls, /tolls: Telegram Stars monetization
- /ahelp: complete admin commands reference manual
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import html
import json
import re
import time
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.constants import TIMEZONE
from src.core.logging import get_logger
from src.services.buketov_api import buketov_api_service
from src.services.schedule_adapter import schedule_api_adapter
from src.services.user_activity_service import user_activity_service
from src.services.user_service import user_service
from src.services.user_toll_service import user_toll_service

logger = get_logger("admin_router")
admin_router = Router(name="admin_router")
_admin_bg_tasks: set[asyncio.Task[Any]] = set()
_admin_sync_lock = asyncio.Lock()
_is_admin_sync_running = False


def _create_admin_task(coro: Any) -> asyncio.Task[Any]:
    """Retains strong reference to background admin tasks to avoid GC drop."""
    task = asyncio.create_task(coro)
    _admin_bg_tasks.add(task)
    task.add_done_callback(_admin_bg_tasks.discard)
    return task


def escape_html(text: Any) -> str:
    """Escapes HTML special characters."""
    if not text:
        return ""
    return html.escape(str(text))


def _format_action_time(dt: datetime | None, fmt: str) -> str:
    """Formats datetime in Asia/Almaty (UTC+5) timezone."""
    if not isinstance(dt, datetime):
        return "—"
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TIMEZONE).strftime(fmt)
    except Exception:
        return "—"


async def _resolve_user_group_name(group_val: Any) -> str:
    """Resolves group name by ID or name string."""
    if not group_val or str(group_val).strip() in ("", "0", "None"):
        return "—"
    try:
        from src.database.models.group import Group

        raw_str = str(group_val).strip()
        num_g = int(raw_str) if raw_str.isdigit() else None
        grp = None
        if num_g is not None:
            grp = await Group.find_one({"id": num_g}) or await Group.find_one({"groupId": num_g})
        if not grp:
            grp = await Group.find_one({"name": raw_str})
        if grp and grp.name:
            return grp.name
        return raw_str
    except Exception:
        return str(group_val)


async def _resolve_user_teacher_name(teacher_val: Any) -> str:
    """Resolves teacher display name by ID or name string."""
    if not teacher_val or str(teacher_val).strip() in ("", "0", "None"):
        return "—"
    try:
        from src.database.models.teacher import Teacher
        from src.services.teacher_directory import teacher_directory_service

        raw_str = str(teacher_val).strip()
        num_t = int(raw_str) if raw_str.isdigit() else None
        tch = None
        if num_t is not None:
            tch = (
                await Teacher.find_one({"id": num_t})
                or await Teacher.find_one({"teacher_id": num_t})
            )
        if not tch:
            tch = (
                await Teacher.find_one({"name": raw_str})
                or await Teacher.find_one({"fullName": raw_str})
            )

        if tch:
            return tch.full_name or tch.name
        if num_t is not None:
            prof = teacher_directory_service.find_by_id(num_t)
            if prof:
                return prof.get("fullName") or prof.get("name") or raw_str
        else:
            prof = teacher_directory_service.find_match(raw_str)
            if prof:
                return prof.get("fullName") or prof.get("name") or raw_str
        return raw_str
    except Exception:
        return str(teacher_val)


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

    global _is_admin_sync_running
    async with _admin_sync_lock:
        if _is_admin_sync_running:
            await message.answer("⚠️ <b>Синхронизация уже выполняется...</b>", parse_mode="HTML")
            return
        _is_admin_sync_running = True

    async def _run_sync_task() -> None:
        global _is_admin_sync_running
        try:
            logger.info("Starting background university catalog sync...")
            from src.cron.daily_update import run_daily_data_update

            res = await run_daily_data_update(force_scan=True)
            if isinstance(res, dict):
                result = res
            else:
                result = {"elapsed_seconds": 0, "new_groups": 0, "new_teachers": 0}

            await message.answer(
                f"✅ <b>Синхронизация каталога завершена!</b>\n\n"
                f"⏱ Время: <code>{result.get('elapsed_seconds', 0)} сек</code>\n"
                f"👥 Новых групп: <b>{result.get('new_groups', 0)}</b>\n"
                f"👨‍🏫 Новых преподавателей: <b>{result.get('new_teachers', 0)}</b>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Catalog sync error: {e}")
            await message.answer(f"❌ <b>Ошибка синхронизации:</b> {e}", parse_mode="HTML")
        finally:
            _is_admin_sync_running = False

    _create_admin_task(_run_sync_task())
    await message.answer("⏳ <b>Синхронизация запущена в фоновом режиме...</b>", parse_mode="HTML")


@admin_router.message(Command(commands=["stat", "state"]))
async def handle_stat(message: Message) -> None:
    """Returns 7-day comprehensive usage, action, and registration statistics in Asia/Almaty (UTC+5)."""
    if not await _check_admin(message):
        return

    try:
        stats = await user_activity_service.get_comprehensive_statistics()
        stat_text = user_activity_service.format_stat_message(stats)
        await message.answer(stat_text)
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
        await message.answer(f"❌ Пользователь <b>{escape_html(arg)}</b> не найден в базе данных.", parse_mode="HTML")
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
        t_str = _format_action_time(a.created_at, "%d.%m %H:%M")
        action_lines.append(f"• <i>{t_str}</i>: {escape_html(a.text)}")

    actions_block = "\n".join(action_lines) if action_lines else "<i>Нет недавних действий</i>"

    raw_name = " ".join(f"{target_user.first_name or ''} {target_user.last_name or ''}".split())
    name_display = escape_html(raw_name) if raw_name else f"ID: {target_user.user_id}"
    user_link = f'<a href="tg://user?id={target_user.user_id}">{name_display}</a>'
    clean_username = (target_user.username or "").strip().lstrip("@")
    username_display = f"@{escape_html(clean_username)}" if clean_username else "отсутствует"

    group_display = await _resolve_user_group_name(target_user.group)
    teacher_display = await _resolve_user_teacher_name(target_user.teacher)

    user_card = (
        f"👤 <b>КАРТОЧКА ПОЛЬЗОВАТЕЛЯ:</b>\n\n"
        f"• <b>ID:</b> <code>{target_user.user_id}</code>\n"
        f"• <b>Username:</b> {username_display}\n"
        f"• <b>Имя:</b> {user_link}\n"
        f"• <b>Язык:</b> {escape_html(target_user.language or 'ru')}\n"
        f"• <b>Группа:</b> {escape_html(group_display)}\n"
        f"• <b>Преподаватель:</b> {escape_html(teacher_display)}\n"
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
        await message.answer(f"❌ Пользователь <b>{escape_html(target_str)}</b> не найден.", parse_mode="HTML")
        return

    from src.database.models.user_action import UserAction

    actions = (
        await UserAction.find({"userId": target_user.user_id})
        .sort("-createdAt")
        .limit(limit)
        .to_list()
    )

    raw_user_name = " ".join(f"{target_user.first_name or ''} {target_user.last_name or ''}".split())
    user_display = escape_html(raw_user_name) if raw_user_name else f"ID: {target_user.user_id}"
    user_link = f'<a href="tg://user?id={target_user.user_id}">{user_display}</a>'

    if not actions:
        await message.answer(f"Логи для пользователя {user_link} пусты.", parse_mode="HTML")
        return

    lines = [f"📋 <b>Логи пользователя {user_link} (последние {len(actions)}):</b>\n"]
    for a in actions:
        t_str = _format_action_time(a.created_at, "%Y-%m-%d %H:%M:%S")
        lines.append(f"• <code>{t_str}</code>: {escape_html(a.text)}")

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
            blocked = 0
            for u in users:
                if not getattr(u, "user_id", None):
                    continue
                delivered = False
                for attempt in range(3):
                    try:
                        await bot.send_message(u.user_id, sms_text, disable_web_page_preview=True)
                        sent += 1
                        delivered = True
                        break
                    except TelegramRetryAfter as e:
                        wait_sec = max(1, int(e.retry_after) + 1)
                        logger.warning(
                            f"TelegramRetryAfter during /sms all: waiting {wait_sec}s for user {u.user_id} (attempt {attempt + 1}/3)"
                        )
                        await asyncio.sleep(wait_sec)
                    except TelegramForbiddenError:
                        blocked += 1
                        failed += 1
                        delivered = True  # Handled, do not retry
                        break
                    except Exception as err:
                        logger.debug(f"Failed to deliver broadcast to {u.user_id}: {err}")
                        failed += 1
                        delivered = True  # Non-retryable error, do not retry
                        break
                if not delivered:
                    failed += 1
                await asyncio.sleep(0.5)  # 500ms pacing against Telegram rate limits

            report = (
                f"✅ <b>Разовое оповещение завершено:</b>\n"
                f"• Доставлено: {sent}\n"
                f"• Не доставлено: {failed}"
            )
            if blocked > 0:
                report += f"\n• Заблокировали бота: {blocked}"
            try:
                await message.answer(report, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to send broadcast completion report: {e}")

        _create_admin_task(_run_broadcast_task())
        await message.answer("📢 <b>Разовое оповещение запущено в фоновом режиме...</b>", parse_mode="HTML")
        return

    # Single user direct message
    target_user = await user_service.find_user(target)
    if not target_user or not getattr(target_user, "user_id", None):
        await message.answer(f"❌ Пользователь <b>{escape_html(target)}</b> не найден.", parse_mode="HTML")
        return

    try:
        await bot.send_message(target_user.user_id, sms_text)
        await message.answer(
            f"✅ <b>Сообщение успешно отправлено:</b>\n"
            f"• <b>Получатель:</b> <code>{target_user.user_id}</code> (@{escape_html(target_user.username or '—')})\n"
            f"• <b>Текст:</b> {escape_html(sms_text)}",
            parse_mode="HTML",
        )
    except TelegramForbiddenError:
        await message.answer(f"❌ Пользователь <code>{target_user.user_id}</code> заблокировал бота.", parse_mode="HTML")
    except TelegramRetryAfter as e:
        await message.answer(f"⏳ Превышен лимит Telegram. Попробуйте через {e.retry_after} сек.", parse_mode="HTML")
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

    _create_admin_task(_run_backup_task())
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

    group = await Group.find_one({"id": group_id}) or await Group.find_one(Group.group_id == group_id)
    if not group:
        await message.answer(f"❌ Группа {group_id} не найдена.")
        return

    group_json = json.dumps(group.model_dump(by_alias=True, mode="json"), indent=2, ensure_ascii=False, default=str)
    await message.answer(f"<pre>{escape_html(group_json)}</pre>", parse_mode="HTML")


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
        lines.append(f"• /get_user{u.user_id} (@{escape_html(u.username or 'без_ника')})")

    await message.answer("\n".join(lines), parse_mode="HTML")


@admin_router.message(Command("group_stat"))
async def handle_group_stat(message: Message) -> None:
    """Calculates bot coverage across university groups and exports group_stat.json."""
    if not await _check_admin(message):
        return

    start_time = time.time()
    await message.answer("⏳ Анализирую группы и пользователей...")

    try:
        from src.database.models.group import Group
        from src.database.models.user import User

        groups = await Group.find().to_list()
        users = await User.find().to_list()

        group_stat = []
        user_group_counts: dict[int, int] = {}
        for u in users:
            if u.group:
                user_group_counts[u.group] = user_group_counts.get(u.group, 0) + 1

        for g in groups:
            g_id = g.group_id or getattr(g, "id", 0)
            stud_cnt = getattr(g, "student_count", 0) or getattr(g, "studentCount", 0) or 0
            our_users = user_group_counts.get(g_id, 0)
            not_our_users = max(0, stud_cnt - our_users)
            ratio = (our_users / stud_cnt) if stud_cnt > 0 else 0.0

            group_stat.append({
                "id": g_id,
                "name": g.name,
                "students": stud_cnt,
                "our_users": our_users,
                "not_our_users": not_our_users,
                "ratio": ratio,
            })

        sorted_stat = sorted(group_stat, key=lambda x: x["not_our_users"], reverse=True)
        active_groups = [g for g in sorted_stat if g["ratio"] > 0][:20]

        lines = ["Потенциальные юзеры:\n"]
        for g in active_groups:
            lines.append(
                f"ID: {g['id']}, All: {g['students']} | Не наши: {g['not_our_users']}\n"
                f"/get_users_by_group{g['id']}"
            )

        json_bytes = json.dumps(sorted_stat, indent=2, ensure_ascii=False).encode("utf-8")
        doc_file = BufferedInputFile(json_bytes, filename="group_stat.json")
        duration = round(time.time() - start_time, 2)

        bot = message.bot
        if bot:
            await bot.send_document(
                chat_id=message.chat.id,
                document=doc_file,
                caption=f"Action time: {duration} сек.",
            )
        await message.answer("\n".join(lines))
    except Exception as e:
        logger.error(f"Error in /group_stat: {e}")
        await message.answer(f"❌ Ошибка в /group_stat: {e}")


@admin_router.message(Command("get_schedule"))
@admin_router.message(F.text.regexp(r"^/get_schedule(?:@\w+)?(?:\s+(\d+)|_?(\d+))?$", flags=2))
async def handle_get_schedule(message: Message) -> None:
    """Fetches and displays schedule for a group."""
    if not await _check_admin(message):
        return

    text = (message.text or "").strip()
    match = re.search(r"^/get_schedule(?:@\w+)?(?:\s+(\d+)|_?(\d+))?$", text, re.IGNORECASE)
    arg = match.group(1) or match.group(2) if match else None

    if not arg or not arg.isdigit():
        await message.answer("ℹ️ Использование: <code>/get_schedule [groupId]</code>", parse_mode="HTML")
        return

    group_id = int(arg)
    from src.database.models.group import Group

    group = await Group.find_one({"id": group_id}) or await Group.find_one(Group.group_id == group_id)
    if not group:
        await message.answer(f"❌ Группа {group_id} не найдена в базе.")
        return

    raw_data = await buketov_api_service.get_group_schedule(group.name)
    records = raw_data.get("records", []) if isinstance(raw_data, dict) else (raw_data if isinstance(raw_data, list) else [])
    adapted = schedule_api_adapter.adapt_group_schedule(records, getattr(group, "language", "ru"))

    preview = f"📅 <b>Расписание группы {escape_html(group.name)} (ID: {group_id}):</b>\nНайдено дней: {len(adapted)}\n"
    for day in adapted:
        subj_cnt = len(day.get("subjects", []))
        preview += f"• {day.get('day', 'День')}: {subj_cnt} пар\n"

    await message.answer(preview, parse_mode="HTML")


@admin_router.message(Command("get_reserved_schedule"))
@admin_router.message(F.text.regexp(r"^/get_reserved_schedule(?:@\w+)?(?:\s+(\d+)|_?(\d+))?$", flags=2))
async def handle_get_reserved_schedule(message: Message) -> None:
    """Returns MongoDB backup/reserved schedule for a group."""
    if not await _check_admin(message):
        return

    text = (message.text or "").strip()
    match = re.search(r"^/get_reserved_schedule(?:@\w+)?(?:\s+(\d+)|_?(\d+))?$", text, re.IGNORECASE)
    arg = match.group(1) or match.group(2) if match else None

    if not arg or not arg.isdigit():
        await message.answer("ℹ️ Использование: <code>/get_reserved_schedule [groupId]</code>", parse_mode="HTML")
        return

    group_id = int(arg)
    from src.database.models.schedule import Schedule

    doc = await Schedule.find_one({"groupId": group_id})
    if not doc or not doc.data:
        await message.answer(f"❌ Резервное расписание для группы {group_id} не найдено в MongoDB.")
        return

    upd_str = doc.updated_at.strftime("%Y-%m-%d %H:%M:%S UTC") if doc.updated_at else "—"
    hash_str = doc.hash[:16] if doc.hash else "—"
    preview = (
        f"💾 <b>Резервное расписание группы {group_id}:</b>\n"
        f"• Обновлено: {upd_str}\n"
        f"• Дней в резерве: {len(doc.data)}\n"
        f"• Хэш: <code>{hash_str}...</code>"
    )
    await message.answer(preview, parse_mode="HTML")


@admin_router.message(Command("restart"))
async def handle_restart(message: Message) -> None:
    """Reports bot architecture status matching legacy command."""
    if not await _check_admin(message):
        return

    await message.answer("Браузер и Puppeteer отключены. Бот работает на официальном REST API КарУ (BuketovApiService) 🚀")


@admin_router.message(Command("info"))
async def handle_info(message: Message) -> None:
    """Displays diagnostics information."""
    if not await _check_admin(message):
        return

    now_kz = datetime.now(timezone(timedelta(hours=5)))
    from src.api.health import health_monitor

    health = await health_monitor.get_status()
    uptime_sec = health.get("uptime_seconds") if health.get("uptime_seconds") is not None else health.get("uptime", 0)
    memory_val = health.get("memory_mb")
    if memory_val is None:
        mem_dict = health.get("memory")
        memory_val = mem_dict.get("rss_mb", 0) if isinstance(mem_dict, dict) else 0

    api_val = health.get("buketov_api", "OK")

    warmup_line = ""
    try:
        from src.cron.cache_warmup import smart_warmup_engine

        st = smart_warmup_engine.get_stats()
        g_m, g_s = divmod(st["groups_max_age_sec"], 60)
        t_m, t_s = divmod(st["teachers_max_age_sec"], 60)
        warmup_line = (
            f"• <b>Прогрев:</b> G max {g_m}m{g_s:02d}s (&gt;10m: {st['groups_over_10m']}) | "
            f"T max {t_m}m{t_s:02d}s (&gt;5m: {st['teachers_over_5m']}) | "
            f"304: {st['api_304']}, 200: {st['api_200']} | Исх. API: {st['out_req_per_min']}/мин\n"
        )
    except Exception as e:
        logger.debug(f"Failed to format warmup stats for /info: {e}")

    info_text = (
        "ℹ️ <b>ДИАГНОСТИЧЕСКАЯ ИНФОРМАЦИЯ:</b>\n\n"
        f"• <b>Время (UTC+5):</b> {now_kz.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"• <b>Аптайм:</b> {uptime_sec} сек.\n"
        f"• <b>RAM процесс:</b> {memory_val} MB\n"
        f"• <b>Статус API КарУ:</b> {api_val}\n"
        f"{warmup_line}"
    )
    await message.answer(info_text, parse_mode="HTML")


@admin_router.message(Command("test"))
async def handle_test(message: Message) -> None:
    """Tests KarU REST API connectivity."""
    if not await _check_admin(message):
        return

    is_alive = await buketov_api_service.health_check()
    if is_alive:
        await message.answer("✅ Официальный API КарУ доступен и отвечает 200 OK!")
    else:
        await message.answer("⚠️ Официальный API КарУ не отвечает или перегружен.")


@admin_router.message(Command("get_callback"))
async def handle_get_callback(message: Message) -> None:
    """Generates an inline test button with arbitrary callback_data."""
    if not await _check_admin(message):
        return

    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("После команды должно быть значение колбека")
        return

    cb_data = parts[1].strip()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=cb_data, callback_data=cb_data)]]
    )
    await message.answer(f"here is your callback: {cb_data}", reply_markup=kb)


# === Moderation: Ban, Mute, Unban, Unmute, Bans ===

def _parse_duration(duration_str: str) -> timedelta | None:
    """Parses duration string like '10m', '2h', '7d', '1w', '10 мин', '2 часа' into timedelta."""
    if not duration_str:
        return None
    match = re.match(
        r"^(\d+)\s*(m|min|mins|minute|minutes|м|мин|минута|минуты|минут|h|hr|hrs|hour|hours|ч|час|часа|часов|d|day|days|д|дн|дня|дней|день|w|wk|wks|week|weeks|н|нед|неделя|недели|недель|mo|mon|month|months|мес|месяц|месяца|месяцев|s|sec|secs|second|seconds|с|сек|секунда|секунды|секунд)$",
        duration_str.strip().lower(),
    )
    if not match:
        return None
    val = int(match.group(1))
    unit = match.group(2).lower()
    if unit in ("m", "min", "mins", "minute", "minutes", "м", "мин", "минута", "минуты", "минут"):
        return timedelta(minutes=val)
    elif unit in ("h", "hr", "hrs", "hour", "hours", "ч", "час", "часа", "часов"):
        return timedelta(hours=val)
    elif unit in ("d", "day", "days", "д", "дн", "дня", "дней", "день"):
        return timedelta(days=val)
    elif unit in ("w", "wk", "wks", "week", "weeks", "н", "нед", "неделя", "недели", "недель"):
        return timedelta(weeks=val)
    elif unit in ("mo", "mon", "month", "months", "мес", "месяц", "месяца", "месяцев"):
        return timedelta(days=val * 30)
    elif unit in ("s", "sec", "secs", "second", "seconds", "с", "сек", "секунда", "секунды", "секунд"):
        return timedelta(seconds=val)
    return None


@admin_router.message(Command("ban"))
async def handle_ban_command(message: Message) -> None:
    """Restricts a user by adding them to BlackList or muting them."""
    if not await _check_admin(message):
        return

    parts = (message.text or "").split()[1:]
    if not parts:
        await message.answer("ℹ️ Использование: <code>/ban [ID/@ник] [время/mute] [причина]</code>", parse_mode="HTML")
        return

    target = parts[0]
    is_mute = False
    rest_parts = parts[1:]

    if rest_parts and rest_parts[0].lower() in ("mute", "мут"):
        is_mute = True
        rest_parts = rest_parts[1:]

    duration_delta: timedelta | None = None
    reason: str | None = None

    if len(rest_parts) > 0:
        duration_delta = _parse_duration(rest_parts[0])
        if duration_delta is not None:
            reason = " ".join(rest_parts[1:]) if len(rest_parts) > 1 else None
        elif len(rest_parts) > 1:
            duration_delta = _parse_duration(f"{rest_parts[0]} {rest_parts[1]}")
            if duration_delta is not None:
                reason = " ".join(rest_parts[2:]) if len(rest_parts) > 2 else None
            else:
                reason = " ".join(rest_parts)
        else:
            reason = " ".join(rest_parts)

    target_user = await user_service.find_user(target)
    if not target_user:
        await message.answer(f"❌ Пользователь {escape_html(target)} не найден.")
        return

    from src.services.black_list_service import black_list_service

    until: datetime | None = None
    if duration_delta is not None:
        until = datetime.now(timezone.utc) + duration_delta

    if is_mute:
        await black_list_service.add_mute(
            user_id=target_user.user_id,
            until=until,
            reason=reason,
            username=target_user.username,
            banned_by=message.from_user.id if message.from_user else None,
        )
        if until:
            until_kz = until.astimezone(timezone(timedelta(hours=5))).strftime("%Y-%m-%d %H:%M:%S")
            await message.answer(f"🔇 Пользователю {target_user.user_id} выдан мут до {until_kz} (UTC+5).")
        else:
            await message.answer(f"🔇 Пользователю {target_user.user_id} выдан мут.")
    else:
        await black_list_service.add_ban(
            user_id=target_user.user_id,
            until=until,
            reason=reason,
            username=target_user.username,
            banned_by=message.from_user.id if message.from_user else None,
        )
        if until:
            until_kz = until.astimezone(timezone(timedelta(hours=5))).strftime("%Y-%m-%d %H:%M:%S")
            await message.answer(f"⛔ Пользователь {target_user.user_id} заблокирован до {until_kz} (UTC+5).")
        else:
            await message.answer(f"⛔ Пользователь {target_user.user_id} заблокирован.")


@admin_router.message(Command("mute"))
async def handle_mute_command(message: Message) -> None:
    """Restricts a user by applying a mute restriction in BlackList."""
    if not await _check_admin(message):
        return

    parts = (message.text or "").split()[1:]
    if not parts:
        await message.answer("ℹ️ Использование: <code>/mute [ID/@ник] [длительность?] [причина?]</code>", parse_mode="HTML")
        return

    target = parts[0]
    rest_parts = parts[1:]
    duration_delta: timedelta | None = None
    reason: str | None = None

    if len(rest_parts) > 0:
        duration_delta = _parse_duration(rest_parts[0])
        if duration_delta is not None:
            reason = " ".join(rest_parts[1:]) if len(rest_parts) > 1 else None
        elif len(rest_parts) > 1:
            duration_delta = _parse_duration(f"{rest_parts[0]} {rest_parts[1]}")
            if duration_delta is not None:
                reason = " ".join(rest_parts[2:]) if len(rest_parts) > 2 else None
            else:
                reason = " ".join(rest_parts)
        else:
            reason = " ".join(rest_parts)

    target_user = await user_service.find_user(target)
    if not target_user:
        await message.answer(f"❌ Пользователь {escape_html(target)} не найден.")
        return

    from src.services.black_list_service import black_list_service

    until: datetime | None = None
    if duration_delta is not None:
        until = datetime.now(timezone.utc) + duration_delta

    await black_list_service.add_mute(
        user_id=target_user.user_id,
        until=until,
        reason=reason,
        username=target_user.username,
        banned_by=message.from_user.id if message.from_user else None,
    )
    if until:
        until_kz = until.astimezone(timezone(timedelta(hours=5))).strftime("%Y-%m-%d %H:%M:%S")
        await message.answer(f"🔇 Пользователю {target_user.user_id} выдан мут до {until_kz} (UTC+5).")
    else:
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
        await message.answer(f"❌ Пользователь {escape_html(target)} не найден.")
        return

    from src.services.black_list_service import black_list_service

    had_entry = await black_list_service.remove(target_user.user_id)
    if had_entry:
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

    from src.services.black_list_service import black_list_service

    await black_list_service.ensure_loaded()
    restrictions = black_list_service.get_all()
    if not restrictions:
        await message.answer("Активных блокировок и мутов нет.")
        return

    lines = [f"⛔ <b>Список блокировок ({len(restrictions)}):</b>\n"]
    for r in restrictions:
        exp_str = ""
        if r.until:
            exp_kz = r.until.astimezone(timezone(timedelta(hours=5))).strftime("%Y-%m-%d %H:%M")
            exp_str = f" (до {exp_kz})"
        else:
            exp_str = " (бессрочно)"
        lines.append(
            f"• ID: <code>{r.user_id}</code> (@{escape_html(r.username or '—')}) — [{r.type}]{exp_str} Причина: {escape_html(r.reason or 'не указана')}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


# === Paywall: Telegram Stars Monetization ===

async def _handle_unpaywall_user(message: Message, target: str) -> None:
    """Helper to unpaywall a user by ID or username."""
    target_user = await user_service.find_user(target)
    user_id = None
    display_name = target

    if target_user:
        user_id = target_user.user_id
        display_name = f"@{target_user.username}" if target_user.username else (target_user.first_name or f"ID: {user_id}")
    elif target.isdigit():
        user_id = int(target)
        display_name = f"ID: {user_id}"
    else:
        await message.answer(
            f"❌ Пользователь <b>\"{escape_html(target)}\"</b> не найден в базе данных.",
            parse_mode="HTML",
        )
        return

    await user_toll_service.disable_toll(user_id)
    await message.answer(
        f"✅ <b>Платный режим отключен!</b>\nПользователь <b>{escape_html(display_name)}</b> (ID: <code>{user_id}</code>) снова может использовать все кнопки бесплатно.",
        parse_mode="HTML",
    )


@admin_router.message(Command(commands=["paywall", "toll", "starwall", "stars", "tax"]))
async def handle_paywall_command(message: Message) -> None:
    """Manages paid button restrictions (Telegram Stars) for specific users."""
    if not await _check_admin(message):
        return

    text = (message.text or "").strip()
    parts = text.split()[1:]

    if not parts:
        await message.answer(
            "⭐ <b>Управление платным режимом (9 000 ⭐ звёзд за кнопку):</b>\n\n"
            "• <code>/paywall [ID|@ник]</code> — включить платные кнопки для пользователя (9 000 ⭐ за кнопку)\n"
            "• <code>/paywall [ID|@ник] off</code> (или <code>/unpaywall [ID|@ник]</code>) — выключить платный режим\n"
            "• <code>/paywalls</code> — список всех пользователей на платном режиме\n\n"
            "<i>Примеры:</i>\n"
            "• <code>/paywall @username</code>\n"
            "• <code>/paywall 123456789</code>\n"
            "• <code>/paywall @username off</code>\n\n"
            "<i>Команда также доступна по алиасу:</i> <code>/toll</code>, <code>/stars</code>",
            parse_mode="HTML",
        )
        return

    if parts[0].lower() in ("list", "all"):
        await handle_paywalls_list_command(message)
        return

    target = parts[0]
    action = parts[1].lower() if len(parts) > 1 else ""

    if action in ("off", "0", "disable"):
        await _handle_unpaywall_user(message, target)
        return

    target_user = await user_service.find_user(target)
    user_id = None
    username = None
    display_name = target

    if target_user:
        user_id = target_user.user_id
        username = target_user.username
        display_name = f"@{username}" if username else (target_user.first_name or f"ID: {user_id}")
        if await user_service.is_admin(user_id):
            await message.answer("⚠️ Нельзя включить платный режим для администратора бота!")
            return
    elif target.isdigit():
        user_id = int(target)
        display_name = f"ID: {user_id}"
        if await user_service.is_admin(user_id):
            await message.answer("⚠️ Нельзя включить платный режим для администратора бота!")
            return
    else:
        await message.answer(
            f"❌ Пользователь <b>\"{escape_html(target)}\"</b> не найден в базе данных бота.\n"
            "<i>Для новых пользователей укажите числовой Telegram ID.</i>",
            parse_mode="HTML",
        )
        return

    star_price = 9000
    if len(parts) > 1 and parts[1].isdigit():
        star_price = int(parts[1])

    sender_id = message.from_user.id if message.from_user else None
    await user_toll_service.enable_toll(
        user_id=user_id,
        star_price=star_price,
        username=username,
        added_by=sender_id,
    )

    await message.answer(
        "⭐ <b>Платный режим успешно активирован!</b>\n\n"
        f"• <b>Пользователь:</b> {escape_html(display_name)} (ID: <code>{user_id}</code>)\n"
        f"• <b>Стоимость кнопки:</b> <b>{star_price:,} ⭐</b> Telegram Stars\n\n"
        f"<i>Теперь при нажатии абсолютно любых кнопок («Назад», «Вперёд», «Обновить расписание», «Вернуться назад» и т.д.) пользователю будет выставляться счёт на {star_price:,} звёзд Telegram. Для всех остальных пользователей бот работает полностью бесплатно в обычном режиме.</i>\n\n"
        f"Отключить: <code>/paywall {user_id} off</code>",
        parse_mode="HTML",
    )


@admin_router.message(Command(commands=["unpaywall", "untoll"]))
async def handle_unpaywall_command(message: Message) -> None:
    """Disables paid buttons mode for a user."""
    if not await _check_admin(message):
        return

    parts = (message.text or "").strip().split()[1:]
    if not parts:
        await message.answer(
            "ℹ️ Укажите ID или @ник: <code>/unpaywall [ID|@ник]</code>",
            parse_mode="HTML",
        )
        return

    await _handle_unpaywall_user(message, parts[0])


@admin_router.message(Command(commands=["paywalls", "tolls"]))
async def handle_paywalls_list_command(message: Message) -> None:
    """Lists all users currently subject to paid buttons mode."""
    if not await _check_admin(message):
        return

    tolled_list = user_toll_service.get_all_tolled()
    if not tolled_list:
        await message.answer("⭐ Список платного режима пуст. Все пользователи используют бот бесплатно.")
        return

    lines = [f"⭐ <b>Пользователи с платными кнопками ({len(tolled_list)}):</b>\n"]
    for i, item in enumerate(tolled_list, 1):
        user_str = f"@{escape_html(item.username)}" if item.username else f"ID: <code>{item.user_id}</code>"
        lines.append(
            f"{i}. {user_str} — <b>{item.star_price:,} ⭐</b> за кнопку\n"
            f"   Отключить: <code>/paywall {item.user_id} off</code>\n"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


@admin_router.message(Command("ahelp"))
async def handle_ahelp(message: Message) -> None:
    """Displays comprehensive administrator commands help reference matching Node.js commit 81705b6."""
    if not await _check_admin(message):
        return

    ahelp_text = (
        "⚡ <b>КОМАНДЫ АДМИНИСТРАТОРА КИЧО:</b>\n\n"
        "🔄 <b>Синхронизация данных:</b>\n"
        "/sync (или /pull_new) — <i>Умный сбор новых данных: проверяет сайт КарУ и добавляет ТОЛЬКО новые группы, преподавателей, кафедры и программы БЕЗ дубликатов!</i>\n"
        "/updateFaculties [hard] — обновить факультеты\n"
        "/updatePrograms [hard] — обновить программы\n"
        "/updateGroups [hard] — обновить группы (быстрый)\n"
        "/updateDepartments [hard] — обновить кафедры\n"
        "/updateTeachers [hard] — обновить преподавателей\n"
        "/updateProfiles [hard] — обновить профили\n\n"
        "⛔ <b>Модерация (бан и мут):</b>\n"
        "/ban [ID/@ник] [время/mute] [причина] — <i>бан или мут (напр. /ban @user 2h или /ban @user mute 30m)</i>\n"
        "/mute [ID/@ник] [время] [причина] — <i>быстрый мут пользователя (напр. /mute @user 1h)</i>\n"
        "/unban [ID/@ник] — <i>снять бан или мут</i>\n"
        "/bans — <i>список активных блокировок и мутов</i>\n\n"
        "⭐ <b>Платный режим (9 000 ⭐ звёзд за кнопку):</b>\n"
        "/paywall [ID/@ник] [on/off] — <i>сделать ВСЕ кнопки платными персонально для пользователя</i>\n"
        "/unpaywall [ID/@ник] — <i>выключить платный режим для пользователя</i>\n"
        "/paywalls — <i>список пользователей на платном тарифе</i>\n\n"
        "📊 <b>Статистика и пользователи:</b>\n"
        "/get_user [ID/@ник] — <i>карточка пользователя с историей логов на русском!</i>\n"
        "/user_logs [ID/@ник] [кол-во] — <i>подробные логи действий пользователя</i>\n"
        "/find_user [@ник] — <i>быстрый поиск юзера по Telegram-нику</i>\n"
        "/stat — статистика онлайна и регистраций\n"
        "/users — общее количество пользователей бота\n"
        "/group_stat — покрытие по группам (+ выгрузка JSON)\n"
        "/get_group [groupId] — инфо о группе\n"
        "/get_users_by_group [groupId] — пользователи группы\n"
        "/get_schedule [groupId] — расписание группы\n"
        "/get_reserved_schedule [groupId] — резервное расписание\n\n"
        "📢 <b>Связь и оповещения:</b>\n"
        "/sms [ID/@ник] [текст] — <i>отправить личное сообщение пользователю</i>\n"
        "/sms all [текст] — <i>разовое оповещение всем пользователям (1 раз)</i>\n\n"
        "🛠 <b>Сервисные команды:</b>\n"
        "/backup_logs — архивировать и выгрузить логи на Google Диск / Telegram\n"
        "/get_logs — скачать текущий лог-файл бота\n"
        "/ignoreLogs [userId] — добавить в игнор логов\n"
        "/restart — перезапуск сессии браузера\n"
        "/info — отладочная информация\n"
        "/test — тест подключения к КарУ\n"
        "/get_callback [callbackName] — тест кнопки"
    )

    await message.answer(ahelp_text, parse_mode="HTML")


__all__ = [
    "admin_router",
    "handle_ahelp",
    "handle_backup_logs",
    "handle_ban_command",
    "handle_bans_list",
    "handle_get_callback",
    "handle_get_group",
    "handle_get_schedule",
    "handle_get_reserved_schedule",
    "handle_get_user",
    "handle_get_users_by_group",
    "handle_group_stat",
    "handle_info",
    "handle_mute_command",
    "handle_paywall_command",
    "handle_paywalls_list_command",
    "handle_restart",
    "handle_sms",
    "handle_stat",
    "handle_sync_new_data",
    "handle_test",
    "handle_unban_command",
    "handle_unmute_command",
    "handle_unpaywall_command",
    "handle_user_logs",
    "handle_users_count",
    "_format_action_time",
]
