import { bot } from "../../../app.js";
import userService from "../../../services/userService.js";
import blackListService from "../../../services/blackListService.js";
import log from "../../../logging/logging.js";

function escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

export function parseDuration(str) {
    if (!str) return null;
    const clean = str.trim().toLowerCase();
    if (['perm', 'permanent', 'forever', 'навсегда', 'вечно', '0'].includes(clean)) {
        return null;
    }
    const match = clean.match(/^(\d+)\s*(m|min|мин|h|hour|ч|час|d|day|д|день|дня|дней|w|week|нед|недель)?$/i);
    if (!match) return null;
    const num = parseInt(match[1], 10);
    const unit = match[2];
    if (!unit || ['m', 'min', 'мин'].includes(unit)) {
        return num * 60 * 1000;
    }
    if (['h', 'hour', 'ч', 'час'].includes(unit)) {
        return num * 60 * 60 * 1000;
    }
    if (['d', 'day', 'д', 'день', 'дня', 'дней'].includes(unit)) {
        return num * 24 * 60 * 60 * 1000;
    }
    if (['w', 'week', 'нед', 'недель'].includes(unit)) {
        return num * 7 * 24 * 60 * 60 * 1000;
    }
    return null;
}

export function formatDurationDate(date) {
    if (!date) return 'навсегда';
    const d = new Date(date);
    return d.toLocaleString('ru-RU', {
        timeZone: 'Asia/Almaty',
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    }) + ' (UTC+5)';
}

export async function handleBanCommand(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        const text = msg.text.trim();
        const parts = text.split(/\s+/).slice(1);

        if (parts.length === 0) {
            return await bot.sendMessage(
                msg.chat.id,
                "ℹ️ <b>Управление блокировками и мутами:</b>\n\n" +
                "• <code>/ban [ID|@ник]</code> — забанить пользователя навсегда\n" +
                "• <code>/ban [ID|@ник] 2h [причина]</code> — забанить на время (10m, 2h, 1d)\n" +
                "• <code>/ban [ID|@ник] mute 30m [причина]</code> — выдать мут\n" +
                "• <code>/mute [ID|@ник] 1h [причина]</code> — быстрая команда мута\n" +
                "• <code>/unban [ID|@ник]</code> (или <code>/ban [ID|@ник] off</code>) — снять бан/мут\n" +
                "• <code>/bans</code> — список всех заблокированных и замученных\n\n" +
                "<i>Примеры:</i>\n" +
                "• <code>/ban @username 1h Спам в поиске</code>\n" +
                "• <code>/ban 123456789 mute 30m Флуд кнопками</code>\n" +
                "• <code>/ban @username off</code>",
                { parse_mode: "HTML" }
            );
        }

        if (parts[0].toLowerCase() === 'list') {
            return await handleBansListCommand(msg);
        }

        const target = parts[0];
        const remaining = parts.slice(1);

        // Снятие бана/мута
        if (remaining[0]?.toLowerCase() === 'off') {
            return await handleUnbanUser(msg, target);
        }

        let isMute = false;
        let durationMs = null;
        let reason = null;

        if (remaining[0]?.toLowerCase() === 'mute') {
            isMute = true;
            if (remaining[1] && parseDuration(remaining[1]) !== null) {
                durationMs = parseDuration(remaining[1]);
                reason = remaining.slice(2).join(' ').trim();
            } else {
                reason = remaining.slice(1).join(' ').trim();
            }
        } else if (remaining[0] && parseDuration(remaining[0]) !== null) {
            durationMs = parseDuration(remaining[0]);
            reason = remaining.slice(1).join(' ').trim();
        } else {
            reason = remaining.join(' ').trim();
        }

        const until = durationMs ? new Date(Date.now() + durationMs) : null;
        await applyRestriction(msg, target, isMute ? 'mute' : 'ban', until, reason);
    } catch (e) {
        log.error("[BanCommand] Ошибка: " + e.message, { stack: e.stack });
        await bot.sendMessage(msg.chat.id, `❌ Ошибка выполнения команды /ban: ${e.message}`);
    }
}

export async function handleMuteCommand(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        const text = msg.text.trim();
        const parts = text.split(/\s+/).slice(1);

        if (parts.length === 0) {
            return await bot.sendMessage(
                msg.chat.id,
                "ℹ️ <b>Формат команды /mute:</b>\n\n" +
                "• <code>/mute [ID|@ник] [длительность] [причина]</code>\n\n" +
                "<i>Примеры:</i>\n" +
                "• <code>/mute @username 30m Спам</code>\n" +
                "• <code>/mute 123456789 2h</code>\n" +
                "• <code>/unmute @username</code> — снять мут",
                { parse_mode: "HTML" }
            );
        }

        const target = parts[0];
        const remaining = parts.slice(1);

        let durationMs = null;
        let reason = null;

        if (remaining[0] && parseDuration(remaining[0]) !== null) {
            durationMs = parseDuration(remaining[0]);
            reason = remaining.slice(1).join(' ').trim();
        } else {
            reason = remaining.join(' ').trim();
        }

        const until = durationMs ? new Date(Date.now() + durationMs) : null;
        await applyRestriction(msg, target, 'mute', until, reason);
    } catch (e) {
        log.error("[MuteCommand] Ошибка: " + e.message, { stack: e.stack });
        await bot.sendMessage(msg.chat.id, `❌ Ошибка выполнения команды /mute: ${e.message}`);
    }
}

export async function handleUnbanCommand(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        const parts = msg.text.trim().split(/\s+/).slice(1);
        if (parts.length === 0) {
            return await bot.sendMessage(msg.chat.id, "ℹ️ Укажите ID или @ник: <code>/unban [ID|@ник]</code>", { parse_mode: "HTML" });
        }

        await handleUnbanUser(msg, parts[0]);
    } catch (e) {
        log.error("[UnbanCommand] Ошибка: " + e.message, { stack: e.stack });
        await bot.sendMessage(msg.chat.id, `❌ Ошибка выполнения /unban: ${e.message}`);
    }
}

async function handleUnbanUser(msg, target) {
    const targetUser = await userService.findUser(target).catch(() => null);
    let userId = null;
    let displayName = target;

    if (targetUser) {
        userId = targetUser.userId;
        displayName = targetUser.username ? `@${targetUser.username}` : (targetUser.firstName || `ID: ${userId}`);
    } else if (/^\d+$/.test(target)) {
        userId = Number(target);
        displayName = `ID: ${userId}`;
    } else {
        return await bot.sendMessage(
            msg.chat.id,
            `❌ Пользователь <b>"${escapeHtml(target)}"</b> не найден в базе данных.`,
            { parse_mode: "HTML" }
        );
    }

    await blackListService.remove(userId);
    await bot.sendMessage(
        msg.chat.id,
        `✅ <b>Ограничения сняты!</b>\nПользователь <b>${escapeHtml(displayName)}</b> (ID: <code>${userId}</code>) успешно разблокирован.`,
        { parse_mode: "HTML" }
    );
}

async function applyRestriction(msg, target, type, until, reason) {
    const targetUser = await userService.findUser(target).catch(() => null);
    let userId = null;
    let username = null;
    let displayName = target;

    if (targetUser) {
        userId = targetUser.userId;
        username = targetUser.username;
        displayName = username ? `@${username}` : (targetUser.firstName || `ID: ${userId}`);

        if (await userService.isAdmin(userId)) {
            return await bot.sendMessage(msg.chat.id, "⚠️ Нельзя применить блокировку к администратору бота!");
        }
    } else if (/^\d+$/.test(target)) {
        userId = Number(target);
        displayName = `ID: ${userId}`;

        if (await userService.isAdmin(userId)) {
            return await bot.sendMessage(msg.chat.id, "⚠️ Нельзя применить блокировку к администратору бота!");
        }
    } else {
        return await bot.sendMessage(
            msg.chat.id,
            `❌ Пользователь <b>"${escapeHtml(target)}"</b> не найден в базе данных бота.\n` +
            `<i>Для новых пользователей укажите числовой Telegram ID.</i>`,
            { parse_mode: "HTML" }
        );
    }

    if (type === 'mute') {
        await blackListService.addMute(userId, {
            until,
            reason,
            username,
            bannedBy: msg.from.id,
        });
    } else {
        await blackListService.addBan(userId, {
            until,
            reason,
            username,
            bannedBy: msg.from.id,
        });
    }

    const typeLabel = type === 'mute' ? '🔇 Мут' : '⛔ Бан';
    const untilText = formatDurationDate(until);
    const reasonText = reason ? escapeHtml(reason) : 'Не указана';

    await bot.sendMessage(
        msg.chat.id,
        `✅ <b>${typeLabel} успешно применён!</b>\n\n` +
        `• <b>Пользователь:</b> ${escapeHtml(displayName)} (ID: <code>${userId}</code>)\n` +
        `• <b>Действует:</b> ${untilText}\n` +
        `• <b>Причина:</b> <i>${reasonText}</i>`,
        { parse_mode: "HTML" }
    );
}

export async function handleBansListCommand(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        const list = blackListService.getAllRestrictions();
        if (!list || list.length === 0) {
            return await bot.sendMessage(msg.chat.id, "📋 Список блокировок пуст. Активных банов и мутов нет.");
        }

        let text = `📋 <b>Активные блокировки и муты (${list.length}):</b>\n\n`;
        for (let i = 0; i < list.length; i++) {
            const item = list[i];
            const icon = item.type === 'mute' ? '🔇' : '⛔';
            const userStr = item.username ? `@${escapeHtml(item.username)}` : `ID: <code>${item.userId}</code>`;
            const untilStr = formatDurationDate(item.until);
            const reasonStr = item.reason ? ` | <i>${escapeHtml(item.reason)}</i>` : '';

            text += `${i + 1}. ${icon} <b>${item.type.toUpperCase()}</b>: ${userStr} (до ${untilStr})${reasonStr}\n` +
                    `   Снять: <code>/unban ${item.userId}</code>\n\n`;
        }

        await bot.sendMessage(msg.chat.id, text, { parse_mode: "HTML" });
    } catch (e) {
        log.error("[BansList] Ошибка: " + e.message, { stack: e.stack });
        await bot.sendMessage(msg.chat.id, `❌ Ошибка получения списка: ${e.message}`);
    }
}
