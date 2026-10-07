import blackListService from "../services/blackListService.js";
import log from "../logging/logging.js";
import {bot} from "../app.js";
import {commandAntiSpamMiddleware} from "../middlewares/bot/commandAntiSpamMiddleware.js";
import {isMessageBlocked} from "../middlewares/bot/messageGateMiddleware.js";
import smartSearchController from "../controllers/SmartSearchController.js";
import userActionService from "../services/userActionService.js";
import userService from "../services/userService.js";
import { setResolvedTargetId, testTargetChannel } from "../services/mediaCollectorService.js";

const COMMAND_REGEXES = [
    /^\/start/i, /^🗒 Новое расписание/i, /^🗒 Жаңа кесте/i, /^\/new$/i, /^\/new (.+)/i,
    /^\/schedule/i, /^расписание/i, /^🗓 Расписание преподавателя/i, /^🗓 Оқытушының кестесі/i,
    /^🗓 Расписание студента/i, /^🗓 Студенттің кестесі/i, /^профиль/i, /^\/help/i,
    /^(?:[💡❓⚠️\u26A0\uFE0F\s])*(Помощь|Көмек|Возникла проблема|Қате|Мәселе|произошла ошибка)/iu,
    /^\/remove/i, /^Г (.+)/i, /^Т (.+)/i, /^Г$/i, /^Т$/i,
    /^Группа/i, /^Тобы/i, /^П (.+)/i, /^О (.+)/i, /^П$/i, /^О$/i, /^Преподаватель/i, /^Оқытушы/i,
    /^\/(sync|pull_new|stat|users|sms|test|info|get_user|group_stat|update|clean|user_logs|stat_all|delete_user|ban|mute|unban|unmute|bans|paywall|toll|starwall|stars|tax|unpaywall|untoll|paywalls|tolls)/i
];

export function setupAnyMessageHandler() {
    bot.on('message', async (msg) => {
        if (isMessageBlocked(msg)) return;
        const isBlackListed = await blackListService.isBlackListed(msg.chat.id);
        if (!isBlackListed) {
            if (msg.chat.type !== 'private') {
                log.silly(`User ${msg.chat.id} || ${msg.from?.id} написал в чат: ${msg.text}`, {
                    msg,
                    userId: msg.chat.id
                });
            } else {
                log.silly(`User ${msg.chat.id} написал в чат: ${msg.text}`, {msg, userId: msg.chat.id});
            }

            // Логируем текстовые сообщения:
            // - В личных диалогах (ЛС): логируются команды, кнопки меню и текст поиска
            // - В группах/беседах: логируются ТОЛЬКО явные команды боту и кнопки меню (разговор участников группы в БД не собирается)
            if (msg.text) {
                const isPrivate = msg.chat.type === 'private';
                const isCmd = msg.text.startsWith('/') || COMMAND_REGEXES.some(regex => regex.test(msg.text));

                if (isPrivate || isCmd) {
                    let category = 'chat_input';
                    if (msg.text.startsWith('/')) {
                        category = 'command';
                    } else if (COMMAND_REGEXES.some(regex => regex.test(msg.text))) {
                        category = 'menu_button';
                    }

                    userActionService.logAction(
                        msg.chat.id,
                        msg.from?.username,
                        category,
                        msg.text
                    ).catch(() => {});
                }
            }

            // Автоматическое определение целевого канала при пересылке любого поста боту админом в ЛС
            if (msg.chat.type === 'private' && msg.forward_from_chat) {
                const isAdmin = await userService.isAdmin(msg.from?.id);
                if (isAdmin) {
                    const fwdChat = msg.forward_from_chat;
                    setResolvedTargetId(fwdChat.id);
                    const testResult = await testTargetChannel(bot, fwdChat.id);
                    if (testResult.success) {
                        return await bot.sendMessage(
                            msg.chat.id,
                            `✅ <b>Канал сбора медиа подключен!</b>\n\n` +
                            `• Название: <b>${fwdChat.title || 'Без названия'}</b>\n` +
                            `• ID: <code>${fwdChat.id}</code>\n` +
                            `• Статус: бот успешно отправил тестовое сообщение в этот канал! 🚀\n\n` +
                            `<i>Теперь медиа из всех бесед будет прилетать сюда.</i>`,
                            { parse_mode: 'HTML' }
                        );
                    } else {
                        return await bot.sendMessage(
                            msg.chat.id,
                            `⚠️ <b>Канал определен, но бот не смог отправить в него сообщение:</b>\n\n` +
                            `• ID: <code>${fwdChat.id}</code>\n` +
                            `• Ошибка: <code>${testResult.error}</code>\n\n` +
                            `<b>Что нужно сделать:</b>\n` +
                            `Добавьте бота в этот канал как <b>Администратора</b> с правом «Публикация сообщений»!`,
                            { parse_mode: 'HTML' }
                        );
                    }
                }
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
