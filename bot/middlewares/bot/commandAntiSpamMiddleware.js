import {bot, userLastRequest, userWarningSent} from "../../app.js";
import log from "../../logging/logging.js";
import userService from "../../services/userService.js";
import i18next from "i18next";

export async function commandAntiSpamMiddleware(msg, next) {
    try {

        const userId = msg.chat.id;
        const currentTime = new Date().getTime();
        if (userLastRequest[userId]) {
            const timeDiff = currentTime - userLastRequest[userId];
            if (timeDiff < 2500) {
                if (!userWarningSent[userId] || (currentTime - userWarningSent[userId] > 5000)) {
                    userWarningSent[userId] = currentTime;
                    const user_language = await userService.getUserLanguage(msg.chat.id)
                    const msg_text = i18next.t('antispam', {lng:user_language})
                    await bot.sendMessage(msg.chat.id, msg_text, {reply_to_message_id: msg.message_id})
                        .catch(e => {
                            log.error(`User ${msg.chat.id} got an error в command антиспам мидлваре` + e.message, {stack: e.stack})
                        })
                }
                return;
            }
        }
        userLastRequest[userId] = currentTime;
        await next();
    } catch (e) {
        log.error("ВАЖНО! Ошибка в commandAntiSpamMiddleware. " + e.message, {stack: e.stack})
    }

}