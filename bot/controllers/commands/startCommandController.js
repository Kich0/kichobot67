import userService from "../../services/userService.js";
import log from "../../logging/logging.js";
import {bot} from "../../app.js";
import {commandAntiSpamMiddleware} from "../../middlewares/bot/commandAntiSpamMiddleware.js";
import i18next from "i18next"
import {criticalErrorController} from "../../exceptions/bot/criticalErrorController.js";

const inline_keyboard = {
    inline_keyboard: [[{text: "Русский", callback_data: "languageIsRu"}], [{
        text: "Қазақ",
        callback_data: "languageIsKz"
    }], [{text: " ", callback_data: "nothing"}, {text: "❌", callback_data: "delete"}, {
        text: " ",
        callback_data: "nothing"
    }]]
}

const msg_text = '🌍 Выберите язык интерфейса. \n' +
    '🌍 Интерфейстің тіл тандаңыз. '

const errorCatch = async (e, msg) => {
    log.error(`ВАЖНО!User ${msg.chat.id}! ОШИБКА В startCommandController. Юзеру сказано что бот прибоел.` + e.message, {stack: e.stack})
    await criticalErrorController(msg)
}

export async function startCommandController(msg) {
    await commandAntiSpamMiddleware(msg, async () => {
        try {
            await bot.sendMessage(msg.chat.id, msg_text, {reply_markup: inline_keyboard, parse_mode: "HTML"})
        } catch (e) {
            await errorCatch(e, msg)
        } finally {
            await userService.registerUser(msg)
                .catch(e => log.error("Ошибка при попытке зарегистрировать пользователя. Юзер никак не пострадал: " + e.message, {
                    stack: e.stack, userId: msg.chat.id
                }))
        }
    });
}

export async function welcomePageRedirectController(call) {
    try{
        const user_language = await userService.getUserLanguage(call.message.chat.id)

        const keyboard = {
            keyboard: [
                [{text: `${i18next.t('new_schedule', {lng:user_language})}`}, {text: `${i18next.t('help', {lng:user_language})}`}],
                [{text: `${i18next.t('teacher_schedule', {lng:user_language})}`}, {text: `${i18next.t('student_schedule', {lng:user_language})}`}],
            ],
            one_time_keyboard: false,
            resize_keyboard: true
        }

        const msg_text = `${i18next.t('welcome_page', {lng:user_language})}`

        await bot.deleteMessage(call.message.chat.id, call.message.message_id)
        await bot.sendMessage(call.message.chat.id, msg_text, {reply_markup: keyboard, parse_mode: "HTML"})
    }catch (e) {
        log.error(`ВАЖНО!User ${call.message.chat.id}! ОШИБКА В welcomePage. Юзеру сказано что бот прибоел.` + e.message, {stack: e.stack})
        await criticalErrorController(call.message)
    }

}