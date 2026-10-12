import userService from "../../../services/userService.js";
import { bot } from "../../../app.js";
import groupService from "../../../services/groupService.js";
import programService from "../../../services/programService.js";
import facultyService from "../../../services/facultyService.js";
import teacherService from "../../../services/teacherService.js";
import ScheduleController from "../../ScheduleController.js";
import userActionService from "../../../services/userActionService.js";
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

export async function getAndSendUserInfo(identifier, toChatId) {
    try {
        let user = await userService.findUser(identifier);

        // Получаем понятные логи действий пользователя (10 последних действий)
        let actions = [];
        const numUserId = Number(String(identifier).trim().replace(/^@/, ''));
        if (user) {
            actions = await userActionService.getUserActions(user.userId, 10);
        } else if (!isNaN(numUserId) && numUserId !== 0) {
            // Если в таблице User нет (например, не завершил регистрацию), но в UserAction логи есть:
            actions = await userActionService.getUserActions(numUserId, 10);
            if (actions.length > 0) {
                user = {
                    userId: numUserId,
                    username: actions[0]?.username || null,
                    firstName: 'В логах бота',
                    lastName: '',
                    userType: 'private',
                    createdAt: actions[actions.length - 1]?.createdAt,
                    updatedAt: actions[0]?.createdAt,
                    language: 'ru'
                };
            }
        }

        if (!user) {
            return await bot.sendMessage(
                toChatId,
                `❌ Пользователь <b>"${escapeHtml(identifier)}"</b> не найден в базе данных Кичо.`,
                { parse_mode: "HTML" }
            );
        }

        const group = user.group ? await groupService.getById(user.group).catch(() => null) : null;
        let group_users = null;
        let program = null;
        let faculty = null;
        if (group) {
            group_users = await userService.getUsersCountByGroupId(group.id).catch(() => null);
            program = group.program ? await programService.getById(group.program).catch(() => null) : null;
            if (program && program.faculty) {
                faculty = await facultyService.getById(program.faculty).catch(() => null);
            }
        }
        const teacher = user.teacher ? await teacherService.getById(user.teacher).catch(() => null) : null;

        const actionsFormatted = userActionService.formatActionsList(actions);

        const usernameText = user.username
            ? `<a href="https://t.me/${escapeHtml(user.username)}">@${escapeHtml(user.username)}</a>`
            : '<i>(не указан)</i>';

        const fullName = [user.firstName, user.lastName].filter(Boolean).join(' ') || 'Без имени';
        const tgLink = `<a href="tg://user?id=${user.userId}">${escapeHtml(fullName)}</a>`;

        let scheduleInfo = '<i>Не выбрано</i>';
        if (group) {
            scheduleInfo = `👥 Группа <b>${escapeHtml(group.name)}</b> (ID: ${group.id})`;
            if (faculty) scheduleInfo += `\n• Факультет: ${escapeHtml(faculty.name)}`;
            if (program) scheduleInfo += `\n• Программа: ${escapeHtml(program.name)}`;
            if (group_users) scheduleInfo += `\n• Студентов группы в боте: <b>${group_users}</b> из ${group.studentCount || '?'}`;
        } else if (teacher) {
            scheduleInfo = `👨‍🏫 Преподаватель <b>${escapeHtml(teacher.name)}</b> (ID: ${teacher.id})`;
        }

        // Определяем точную последнюю активность: берём самое свежее время между действиями в UserAction и user.updatedAt
        let latestActiveDate = null;
        if (actions && actions.length > 0 && actions[0].createdAt) {
            latestActiveDate = new Date(actions[0].createdAt);
        }
        if (user.updatedAt) {
            const userUpdated = new Date(user.updatedAt);
            if (!latestActiveDate || userUpdated > latestActiveDate) {
                latestActiveDate = userUpdated;
            }
        }
        if (!latestActiveDate && user.createdAt) {
            latestActiveDate = new Date(user.createdAt);
        }

        const regDate = user.createdAt ? userActionService.formatDate(user.createdAt) : 'Неизвестно';
        let lastActive = 'Неизвестно';
        if (latestActiveDate && !isNaN(latestActiveDate.getTime())) {
            const elapsed = ScheduleController.formatElapsedTime(latestActiveDate.getTime(), 'ru');
            const absoluteKz = userActionService.formatDate(latestActiveDate);
            lastActive = `${elapsed} назад (<code>${absoluteKz}</code>)`;
        }

        const langText = user.language === 'kz' ? '🇰🇿 Казахский (kz)' : '🇷🇺 Русский (ru)';

        let msgText = `👤 <b>Карточка пользователя Telegram:</b>\n\n` +
            `• <b>Имя в TG:</b> ${tgLink}\n` +
            `• <b>Ник:</b> ${usernameText}\n` +
            `• <b>Telegram ID:</b> <code>${user.userId}</code>\n` +
            `• <b>Тип чата:</b> ${escapeHtml(user.userType || 'private')}\n` +
            `• <b>Язык бота:</b> ${langText}\n` +
            `• <b>Текущее расписание:</b>\n  ${scheduleInfo}\n\n` +
            `📅 <b>Регистрация:</b> <code>${regDate}</code>\n` +
            `⏱ <b>Последняя активность:</b> ${lastActive}\n\n` +
            `📜 <b>История действий (время Казахстана UTC+5):</b>\n${actionsFormatted}\n\n` +
            `💬 <b>Написать личное сообщение через бота:</b>\n` +
            `<code>/sms ${user.username ? '@' + user.username : user.userId} [текст сообщения]</code>`;

        await bot.sendMessage(toChatId, msgText, {
            parse_mode: "HTML",
            disable_web_page_preview: true
        });

    } catch (e) {
        log.error("[getUser] Ошибка при формировании инфо о пользователе: " + e.message, { stack: e.stack });
        await bot.sendMessage(toChatId, `❌ Ошибка при получении пользователя: ${escapeHtml(e.message)}`);
    }
}

// Для совместимости со старыми вызовами
export async function getAndSendUserInfoByUserId(userId, toChatId) {
    return await getAndSendUserInfo(userId, toChatId);
}

export async function getUserCommandController(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        // Извлекаем аргумент: /get_user @username, /get_user 12345, /get_user12345, /get_user@bot 12345, /find_user @username
        let identifier = msg.text
            .replace(/^\/(?:get_user|find_user|user)(?:@\w+)?(?:\s+|_)?/i, '')
            .trim();

        if (!identifier) {
            return await bot.sendMessage(
                msg.chat.id,
                "ℹ️ <b>Укажите Telegram ID или ник пользователя:</b>\n\n" +
                "Примеры:\n" +
                "• <code>/get_user @username</code>\n" +
                "• <code>/get_user username</code>\n" +
                "• <code>/get_user 123456789</code>\n" +
                "• <code>/find_user @username</code>",
                { parse_mode: "HTML" }
            );
        }

        await getAndSendUserInfo(identifier, msg.chat.id);
    } catch (e) {
        log.error("Ошибочка при /get_user: " + e.message, { stack: e.stack });
    }
}

export async function getUserLogsCommandController(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
        }

        // Поддержка: /user_logs @username 20, /user_logs 12345 50, /user_logs@bot @username, /user_logs12345
        const rawArgs = msg.text.replace(/^\/user_logs(?:@\w+)?(?:\s+|_)?/i, '').trim();
        const parts = rawArgs.split(/\s+/).filter(Boolean);
        const identifier = parts[0];
        const limit = parseInt(parts[1], 10) || 25;

        if (!identifier) {
            return await bot.sendMessage(
                msg.chat.id,
                "ℹ️ <b>Использование команды логов:</b>\n" +
                "<code>/user_logs [userId или @username] [кол-во]</code>\n\n" +
                "Пример: <code>/user_logs @username 20</code>",
                { parse_mode: "HTML" }
            );
        }

        let user = await userService.findUser(identifier);
        let actions = [];
        const numUserId = Number(String(identifier).trim().replace(/^@/, ''));
        if (user) {
            actions = await userActionService.getUserActions(user.userId, limit);
        } else if (!isNaN(numUserId) && numUserId !== 0) {
            actions = await userActionService.getUserActions(numUserId, limit);
            if (actions.length > 0) {
                user = {
                    userId: numUserId,
                    username: actions[0]?.username || null,
                    firstName: 'В логах бота'
                };
            }
        }

        if (!user) {
            return await bot.sendMessage(
                msg.chat.id,
                `❌ Пользователь <b>"${escapeHtml(identifier)}"</b> не найден.`,
                { parse_mode: "HTML" }
            );
        }

        const actionsFormatted = userActionService.formatActionsList(actions);
        const userTitle = user.username ? '@' + escapeHtml(user.username) : escapeHtml(user.firstName || 'Без имени');
        const title = `📜 <b>Логи действий пользователя ${userTitle} (ID: <code>${user.userId}</code>, записей: ${actions.length}):</b>\n\n`;

        const fullMessage = title + actionsFormatted;
        if (fullMessage.length <= 4000) {
            await bot.sendMessage(msg.chat.id, fullMessage, { parse_mode: "HTML" });
        } else {
            const lines = actionsFormatted.split('\n');
            let currentChunk = title;
            for (const line of lines) {
                if ((currentChunk + '\n' + line).length > 3800) {
                    await bot.sendMessage(msg.chat.id, currentChunk, { parse_mode: "HTML" });
                    currentChunk = '';
                }
                currentChunk += (currentChunk ? '\n' : '') + line;
            }
            if (currentChunk) {
                await bot.sendMessage(msg.chat.id, currentChunk, { parse_mode: "HTML" });
            }
        }
    } catch (e) {
        log.error("Ошибочка при /user_logs: " + e.message, { stack: e.stack });
    }
}