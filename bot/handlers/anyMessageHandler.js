import blackListService from "../services/blackListService.js";
import log from "../logging/logging.js";
import {bot} from "../app.js";
import {commandAntiSpamMiddleware} from "../middlewares/bot/commandAntiSpamMiddleware.js";
import {isMessageBlocked} from "../middlewares/bot/messageGateMiddleware.js";
import smartSearchController from "../controllers/SmartSearchController.js";

const COMMAND_REGEXES = [
    /^\/start/i, /^🗒 Новое расписание/i, /^🗒 Жаңа кесте/i, /^\/new$/i, /^\/new (.+)/i,
    /^\/schedule/i, /^расписание/i, /^🗓 Расписание преподавателя/i, /^🗓 Оқытушының кестесі/i,
    /^🗓 Расписание студента/i, /^🗓 Студенттің кестесі/i, /^профиль/i, /^\/help/i,
    /^(?:[💡❓⚠️\u26A0\uFE0F\s])*(Помощь|Көмек|Возникла проблема|Қате|Мәселе|произошла ошибка)/iu,
    /^\/remove/i, /^Г (.+)/i, /^Т (.+)/i, /^Г$/i, /^Т$/i,
    /^Группа/i, /^Тобы/i, /^П (.+)/i, /^О (.+)/i, /^П$/i, /^О$/i, /^Преподаватель/i, /^Оқытушы/i,
    /^\/search/i, /^Поиск/i,
    /^(сикс|север|севен|six|seven|67|шестьдесят семь)/i,
    /^\/(sync|pull_new|stat|users|sms|test|info|get_user|group_stat|update|clean|user_logs|stat_all|delete_user)/i
];

export function setupAnyMessageHandler() {
    bot.on('message', async (msg) => {
        if (isMessageBlocked(msg)) return;
        const isBlackListed = await blackListService.isBlackListed(msg.chat.id)
        if (!isBlackListed) {
            if (msg.chat.type !== 'private') {
                log.silly(`User ${msg.chat.id} || ${msg.from?.id} написал в чат: ${msg.text}`, {
                    msg,
                    userId: msg.chat.id
                })
            } else {
                log.silly(`User ${msg.chat.id} написал в чат: ${msg.text}`, {msg, userId: msg.chat.id})
            }

            if (msg.chat.type === 'private' && msg.text) {
                // Все сообщения со слешем '/' — это команды Telegram (пользовательские, админские или сервисные).
                // Умный поиск НИКОГДА не должен обрабатывать слеш-команды!
                if (msg.text.startsWith('/')) {
                    return;
                }

                const isCommand = COMMAND_REGEXES.some(regex => regex.test(msg.text));
                
                if (!isCommand) {
                    await commandAntiSpamMiddleware(msg, async () => {
                        await smartSearchController.handleTextSearch(msg);
                    });
                }
            }
        }
    });
}
