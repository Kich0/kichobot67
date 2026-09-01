import {bot} from "../../app.js";
import log from "../../logging/logging.js";
import userService from "../../services/userService.js";
import i18next from "i18next";

const COMMAND_COOLDOWN_MS = 500;
const WARNING_COOLDOWN_MS = 4000;

const userLastCommand = {};
const userCommandWarningSent = {};

setInterval(() => {
    const now = Date.now();
    for (const uid in userLastCommand) {
        if (now - userLastCommand[uid] > 60000) delete userLastCommand[uid];
    }
    for (const uid in userCommandWarningSent) {
        if (now - userCommandWarningSent[uid] > 60000) delete userCommandWarningSent[uid];
    }
}, 3 * 60 * 1000);

export async function commandAntiSpamMiddleware(msg, next) {
    try {
        const userId = msg.chat?.id;
        if (!userId) return;

        const currentTime = Date.now();
        if (userLastCommand[userId]) {
            const timeDiff = currentTime - userLastCommand[userId];
            if (timeDiff < COMMAND_COOLDOWN_MS) {
                if (!userCommandWarningSent[userId] || (currentTime - userCommandWarningSent[userId] > WARNING_COOLDOWN_MS)) {
                    userCommandWarningSent[userId] = currentTime;
                    const user_language = await userService.getUserLanguage(userId);
                    const msg_text = i18next.t('antispam', {lng: user_language});
                    await bot.sendMessage(userId, msg_text, {reply_to_message_id: msg.message_id})
                        .catch(e => {
                            log.error(`User ${userId} got an error в command антиспам мидлваре: ` + e.message, {stack: e.stack});
                        });
                }
                return;
            }
        }
        userLastCommand[userId] = currentTime;
        await next();
    } catch (e) {
        log.error("ВАЖНО! Ошибка в commandAntiSpamMiddleware. " + e.message, {stack: e.stack});
    }
}