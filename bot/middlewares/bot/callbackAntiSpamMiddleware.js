import i18next from "i18next";
import {bot, userLastRequest, userWarningSent} from "../../app.js";
import log from "../../logging/logging.js";
import userService from "../../services/userService.js";
import {isUserBanned} from "./messageGateMiddleware.js";

export async function callbackAntiSpamMiddleware(call, next) {
    try {
        const userId = call.message?.chat?.id;
        if (!userId) return;
        if (isUserBanned(userId)) return;
        const currentTime = new Date().getTime();
        if (userLastRequest[userId]) {
            const timeDiff = currentTime - userLastRequest[userId];

            if (timeDiff < 1500) {
                if (!userWarningSent[userId] || (currentTime - userWarningSent[userId] > 5000)) {
                    userWarningSent[userId] = currentTime;
                    const user_language = await userService.getUserLanguage(call.message.chat.id)
                    const msg_text = i18next.t('antispam', {lng:user_language})

                    await bot.answerCallbackQuery(call.id, {text: msg_text, show_alert: false})
                        .catch(async (e) => {
                            try {
                                log.error(`User ${call.message.chat.id} got an error в коллбек антиспам мидлваре` + e.message, {stack: e.stack})
                                await bot.deleteMessage(call.message.chat.id, call.message.message_id)
                                await bot.sendMessage(call.message.chat.id, "⚠️Произошла ошибка! Попробуйте получить ваше меню снова.")
                            } catch (e) {
                                log.error(`User ${call.message.chat.id} got an double!!! error в коллбек антиспам мидлваре` + e.message, {stack: e.stack})
                            }
                        })
                }
                return;
            }
        }
        userLastRequest[userId] = currentTime;
        await next();
    } catch (e) {
        log.error(`User ${call.message.chat.id} got an error в коллбек антиспам мидлваре` + e.message, {stack: e.stack})
    }

}