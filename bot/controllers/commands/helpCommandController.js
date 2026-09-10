import {bot} from "../../app.js";
import log from "../../logging/logging.js";
import userService from "../../services/userService.js";
import i18next from "i18next";
import {criticalErrorController} from "../../exceptions/bot/criticalErrorController.js";
import {commandAntiSpamMiddleware} from "../../middlewares/bot/commandAntiSpamMiddleware.js";

const errorCatch = async (e, msg) => {
    log.error(`ВАЖНО!User ${msg.chat.id}! ОШИБКА В helpCommandController. Юзеру сказано что бот прибоел.` + e.message, {
        stack: e.stack,
        userId: msg.chat.id
    })
    await criticalErrorController(msg)
}

export async function helpCommandController(msg) {
    await commandAntiSpamMiddleware(msg, async () => {
        try {
            const user_language = await userService.getUserLanguage(msg.chat.id)

            const msg_text = i18next.t('help_command_content', {lng:user_language})
            const markup = {
                inline_keyboard: [
                    [{ text: `✍️ ${i18next.t('write_to_dev', {lng:user_language})}`, url: "https://t.me/Kicho_0" }]
                ]
            }
            await bot.sendMessage(msg.chat.id, msg_text, {
                reply_markup: markup,
                parse_mode: "HTML",
                disable_web_page_preview: true
            })
        } catch (e) {
            await errorCatch(e, msg)
        }
    })
}