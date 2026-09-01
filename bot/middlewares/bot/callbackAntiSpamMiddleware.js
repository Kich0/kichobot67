import i18next from "i18next";
import {bot} from "../../app.js";
import log from "../../logging/logging.js";
import userService from "../../services/userService.js";
import {isUserBanned} from "./messageGateMiddleware.js";

const CALLBACK_DEBOUNCE_MS = 200;       // Защита от случайного физического дабл-клика
const CALLBACK_BURST_WINDOW_MS = 3000;  // Скользящее окно анализа спама (3 сек)
const CALLBACK_BURST_MAX = 8;           // Максимум 8 кликов за 3 секунды
const WARNING_COOLDOWN_MS = 4000;       // Интервал между предупреждениями

const userLastCallback = {};
const userCallbackClicks = {};
const userCallbackWarningSent = {};

// Периодическая очистка старых данных (каждые 3 минуты)
setInterval(() => {
    const now = Date.now();
    for (const uid in userCallbackClicks) {
        userCallbackClicks[uid] = userCallbackClicks[uid].filter(t => now - t < CALLBACK_BURST_WINDOW_MS);
        if (!userCallbackClicks[uid].length) delete userCallbackClicks[uid];
    }
    for (const uid in userLastCallback) {
        if (now - userLastCallback[uid] > 60000) delete userLastCallback[uid];
    }
    for (const uid in userCallbackWarningSent) {
        if (now - userCallbackWarningSent[uid] > 60000) delete userCallbackWarningSent[uid];
    }
}, 3 * 60 * 1000);

export async function callbackAntiSpamMiddleware(call, next) {
    try {
        const userId = call.message?.chat?.id;
        if (!userId) return;
        if (isUserBanned(userId)) return;

        const currentTime = Date.now();

        // 1. Дебаунс от быстрого дабл-клика (< 200мс) — тихо подтверждаем callback без спам-сообщения
        if (userLastCallback[userId]) {
            const timeDiff = currentTime - userLastCallback[userId];
            if (timeDiff < CALLBACK_DEBOUNCE_MS) {
                await bot.answerCallbackQuery(call.id).catch(() => {});
                return;
            }
        }

        // 2. Проверка на спам-флуд (скользящее окно)
        if (!userCallbackClicks[userId]) userCallbackClicks[userId] = [];
        userCallbackClicks[userId].push(currentTime);
        userCallbackClicks[userId] = userCallbackClicks[userId].filter(t => currentTime - t < CALLBACK_BURST_WINDOW_MS);

        if (userCallbackClicks[userId].length > CALLBACK_BURST_MAX) {
            if (!userCallbackWarningSent[userId] || (currentTime - userCallbackWarningSent[userId] > WARNING_COOLDOWN_MS)) {
                userCallbackWarningSent[userId] = currentTime;
                const user_language = await userService.getUserLanguage(userId);
                const msg_text = i18next.t('antispam', { lng: user_language });

                await bot.answerCallbackQuery(call.id, { text: msg_text, show_alert: false })
                    .catch(async (e) => {
                        try {
                            log.error(`User ${userId} got an error в коллбек антиспам мидлваре: ` + e.message, { stack: e.stack });
                            await bot.deleteMessage(userId, call.message.message_id);
                            await bot.sendMessage(userId, "⚠️Произошла ошибка! Попробуйте получить ваше меню снова.");
                        } catch (err) {
                            log.error(`User ${userId} got a double error в коллбек антиспам мидлваре: ` + err.message, { stack: err.stack });
                        }
                    });
            } else {
                await bot.answerCallbackQuery(call.id).catch(() => {});
            }
            return;
        }

        userLastCallback[userId] = currentTime;
        await next();
    } catch (e) {
        log.error(`User ${call.message?.chat?.id} got an error в коллбек антиспам мидлваре: ` + e.message, { stack: e.stack });
    }
}