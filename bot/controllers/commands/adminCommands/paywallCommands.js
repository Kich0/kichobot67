import { bot } from "../../../app.js";
import userService from "../../../services/userService.js";
import userTollService from "../../../services/userTollService.js";
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

export async function handlePaywallCommand(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        const text = msg.text.trim();
        const parts = text.split(/\s+/).slice(1);

        if (parts.length === 0) {
            return await bot.sendMessage(
                msg.chat.id,
                "⭐ <b>Управление платным режимом (9 000 ⭐ звёзд за кнопку):</b>\n\n" +
                "• <code>/paywall [ID|@ник]</code> — включить платные кнопки для пользователя (9 000 ⭐ за кнопку)\n" +
                "• <code>/paywall [ID|@ник] off</code> (или <code>/unpaywall [ID|@ник]</code>) — выключить платный режим\n" +
                "• <code>/paywalls</code> — список всех пользователей на платном режиме\n\n" +
                "<i>Примеры:</i>\n" +
                "• <code>/paywall @username</code>\n" +
                "• <code>/paywall 123456789</code>\n" +
                "• <code>/paywall @username off</code>\n\n" +
                "<i>Команда также доступна по алиасу:</i> <code>/toll</code>, <code>/stars</code>",
                { parse_mode: "HTML" }
            );
        }

        if (parts[0].toLowerCase() === 'list') {
            return await handlePaywallsListCommand(msg);
        }

        const target = parts[0];
        const action = parts[1]?.toLowerCase();

        if (action === 'off') {
            return await handleUnpaywallUser(msg, target);
        }

        // Включение платного режима
        const targetUser = await userService.findUser(target).catch(() => null);
        let userId = null;
        let username = null;
        let displayName = target;

        if (targetUser) {
            userId = targetUser.userId;
            username = targetUser.username;
            displayName = username ? `@${username}` : (targetUser.firstName || `ID: ${userId}`);

            if (await userService.isAdmin(userId)) {
                return await bot.sendMessage(msg.chat.id, "⚠️ Нельзя включить платный режим для администратора бота!");
            }
        } else if (/^\d+$/.test(target)) {
            userId = Number(target);
            displayName = `ID: ${userId}`;

            if (await userService.isAdmin(userId)) {
                return await bot.sendMessage(msg.chat.id, "⚠️ Нельзя включить платный режим для администратора бота!");
            }
        } else {
            return await bot.sendMessage(
                msg.chat.id,
                `❌ Пользователь <b>"${escapeHtml(target)}"</b> не найден в базе данных бота.\n` +
                `<i>Для новых пользователей укажите числовой Telegram ID.</i>`,
                { parse_mode: "HTML" }
            );
        }

        const starPrice = 9000;
        await userTollService.enableToll(userId, {
            starPrice,
            username,
            addedBy: msg.from.id,
        });

        await bot.sendMessage(
            msg.chat.id,
            `⭐ <b>Платный режим успешно активирован!</b>\n\n` +
            `• <b>Пользователь:</b> ${escapeHtml(displayName)} (ID: <code>${userId}</code>)\n` +
            `• <b>Стоимость кнопки:</b> <b>${starPrice.toLocaleString('ru-RU')} ⭐</b> Telegram Stars\n\n` +
            `<i>Теперь при нажатии абсолютно любых кнопок («Назад», «Вперёд», «Обновить расписание», «Вернуться назад» и т.д.) пользователю будет выставляться счёт на ${starPrice.toLocaleString('ru-RU')} звёзд Telegram. Для всех остальных пользователей бот работает полностью бесплатно в обычном режиме.</i>\n\n` +
            `Отключить: <code>/paywall ${userId} off</code>`,
            { parse_mode: "HTML" }
        );
    } catch (e) {
        log.error("[PaywallCommand] Ошибка: " + e.message, { stack: e.stack });
        await bot.sendMessage(msg.chat.id, `❌ Ошибка выполнения /paywall: ${e.message}`);
    }
}

export async function handleUnpaywallCommand(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        const parts = msg.text.trim().split(/\s+/).slice(1);
        if (parts.length === 0) {
            return await bot.sendMessage(msg.chat.id, "ℹ️ Укажите ID или @ник: <code>/unpaywall [ID|@ник]</code>", { parse_mode: "HTML" });
        }

        await handleUnpaywallUser(msg, parts[0]);
    } catch (e) {
        log.error("[UnpaywallCommand] Ошибка: " + e.message, { stack: e.stack });
        await bot.sendMessage(msg.chat.id, `❌ Ошибка выполнения /unpaywall: ${e.message}`);
    }
}

async function handleUnpaywallUser(msg, target) {
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

    await userTollService.disableToll(userId);
    await bot.sendMessage(
        msg.chat.id,
        `✅ <b>Платный режим отключен!</b>\nПользователь <b>${escapeHtml(displayName)}</b> (ID: <code>${userId}</code>) снова может использовать все кнопки бесплатно.`,
        { parse_mode: "HTML" }
    );
}

export async function handlePaywallsListCommand(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        const list = userTollService.getAllTolled();
        if (!list || list.length === 0) {
            return await bot.sendMessage(msg.chat.id, "⭐ Список платного режима пуст. Все пользователи используют бот бесплатно.");
        }

        let text = `⭐ <b>Пользователи с платными кнопками (${list.length}):</b>\n\n`;
        for (let i = 0; i < list.length; i++) {
            const item = list[i];
            const userStr = item.username ? `@${escapeHtml(item.username)}` : `ID: <code>${item.userId}</code>`;
            text += `${i + 1}. ${userStr} — <b>${item.starPrice.toLocaleString('ru-RU')} ⭐</b> за кнопку\n` +
                    `   Отключить: <code>/paywall ${item.userId} off</code>\n\n`;
        }

        await bot.sendMessage(msg.chat.id, text, { parse_mode: "HTML" });
    } catch (e) {
        log.error("[PaywallsList] Ошибка: " + e.message, { stack: e.stack });
        await bot.sendMessage(msg.chat.id, `❌ Ошибка получения списка: ${e.message}`);
    }
}
