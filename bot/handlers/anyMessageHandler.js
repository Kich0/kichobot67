import blackListService from "../services/blackListService.js";
import log from "../logging/logging.js";
import {bot} from "../app.js";
import {commandAntiSpamMiddleware} from "../middlewares/bot/commandAntiSpamMiddleware.js";
import userService from "../services/userService.js";
import i18next from "i18next";
import {getRandomMemeResponse} from "../controllers/commands/memeResponseController.js";

const COMMAND_REGEXES = [
    /^\/start/i, /^🗒 Новое расписание/i, /^🗒 Жаңа кесте/i, /^\/new$/i, /^\/new (.+)/i,
    /^\/schedule/i, /^расписание/i, /^🗓 Расписание преподавателя/i, /^🗓 Оқытушының кестесі/i,
    /^🗓 Расписание студента/i, /^🗓 Студенттің кестесі/i, /^профиль/i, /^\/help/i,
    /^💡 Помощь/i, /^💡 Көмек/i, /^\/remove/i, /^Г (.+)/i, /^Т (.+)/i, /^Г$/i, /^Т$/i,
    /^Группа/i, /^Тобы/i, /^П (.+)/i, /^О (.+)/i, /^П$/i, /^О$/i, /^Преподаватель/i, /^Оқытушы/i,
    /^\/search/i, /^Поиск/i, /^\/news/i, /^\/donate/i,
    /^(сикс|север|севен|six|seven|67|шестьдесят семь)/i
];

export function setupAnyMessageHandler() {
    bot.on('message', async (msg) => {
        const isBlackListed = await blackListService.isBlackListed(msg.chat.id)
        if (!isBlackListed) {
            if (msg.chat.type !== 'private') {
                log.silly(`User ${msg.chat.id} || ${msg.from.id} написал в чат: ${msg.text}`, {
                    msg,
                    userId: msg.chat.id
                })
            } else {
                log.silly(`User ${msg.chat.id} написал в чат: ${msg.text}`, {msg, userId: msg.chat.id})
            }

            if (msg.chat.type === 'private' && msg.text) {
                const isCommand = COMMAND_REGEXES.some(regex => regex.test(msg.text));
                
                if (!isCommand) {
                    await commandAntiSpamMiddleware(msg, async () => {
                        try {
                            const user_language = await userService.getUserLanguage(msg.chat.id);
                            const keyboard = {
                                keyboard: [
                                    [{text: `${i18next.t('new_schedule', {lng:user_language})}`}, {text: `${i18next.t('help', {lng:user_language})}`}],
                                    [{text: `${i18next.t('teacher_schedule', {lng:user_language})}`}, {text: `${i18next.t('student_schedule', {lng:user_language})}`}],
                                ],
                                one_time_keyboard: false,
                                resize_keyboard: true
                            };
                            const memeText = getRandomMemeResponse();
                            await bot.sendMessage(msg.chat.id, memeText, {reply_markup: keyboard});
                        } catch (e) {
                            log.error(`User ${msg.chat.id} error in text fallback: ${e.message}`, {stack: e.stack});
                        }
                    });
                }
            }
        }
    });
}

