import log from "../logging/logging.js";
import config from "../config.js";
import {bot} from "../app.js";
import {isMessageBlocked} from "../middlewares/bot/messageGateMiddleware.js";
import userTollService from "../services/userTollService.js";
import userActionService from "../services/userActionService.js";


export function safeHandler(handler, handlerName = 'unknown') {
    return async (...args) => {
        const msg = args[0];
        if (msg && isMessageBlocked(msg)) return;

        const userId = msg?.chat?.id || msg?.from?.id;
        if (userId && handlerName !== 'start' && userTollService.isUserTolled(userId)) {
            if (userTollService.hasPaidCredit(userId)) {
                userTollService.consumePaidCredit(userId);
                userActionService.logAction(
                    userId,
                    msg?.from?.username,
                    'toll_menu_paid_click',
                    `Оплаченная кнопка «${msg.text}» (${handlerName})`
                ).catch(() => {});
            } else {
                const price = userTollService.getTollPrice(userId);
                await bot.sendMessage(
                    userId,
                    `⭐ <b>Платное действие</b>\n\n` +
                    `Использование этой кнопки стоит <b>${price.toLocaleString('ru-RU')} ⭐ звёзд Telegram</b>.\n` +
                    `Для продолжения оплатите счёт ниже 👇`,
                    { parse_mode: 'HTML' }
                ).catch(() => {});

                await bot.sendInvoice(
                    userId,
                    "⭐ Платная кнопка",
                    `Действие: ${msg.text || handlerName} (${price.toLocaleString('ru-RU')} ⭐)`,
                    `toll_msg_${userId}_${Date.now()}`,
                    "",
                    "XTR",
                    [{ label: "Действие в боте", amount: price }]
                ).catch(e => log.error(`[UserToll] Ошибка отправки счёта: ${e.message}`));

                userActionService.logAction(
                    userId,
                    msg?.from?.username,
                    'toll_menu_blocked',
                    `Кнопка «${msg.text}» (${handlerName}) — счёт ${price} ⭐`
                ).catch(() => {});

                return;
            }
        }
        try {
            await handler(...args);
        } catch (error) {
            let userId = 'unknown';
            if (args[0]?.chat?.id) {
                userId = args[0].chat.id;
            } else if (args[0]?.message?.chat?.id) {
                userId = args[0].message.chat.id;
            }

            log.error(`Error in ${handlerName} handler`, {
                userId,
                error: error.message,
                stack: error.stack,
                handlerName
            });
            if (!config.DEBUG) {
                bot.sendMessage(config.LOG_CHANEL_ID,
                    `⚠️ Error in handler: ${handlerName}\n\nUser: ${userId}\nError: ${error.message}`
                ).catch(e => log.error('Failed to send error notification', { stack: e.stack }));
            }
            try {
                const chatId = args[0]?.chat?.id || args[0]?.message?.chat?.id;
                if (chatId) {
                    await bot.sendMessage(chatId,
                        'Произошла ошибка при обработке команды. Попробуйте еще раз или обратитесь к администратору.'
                    );
                }
            } catch (e) {
                log.error('Failed to send error message to user', { stack: e.stack });
            }
        }
    };
}


export function safeCallbackHandler(handler, handlerName = 'unknown') {
    return async (call) => {
        try {
            await handler(call);
        } catch (error) {
            const userId = call?.message?.chat?.id || 'unknown';

            log.error(`Error in ${handlerName} callback handler`, {
                userId,
                error: error.message,
                stack: error.stack,
                data: call?.data,
                handlerName
            });
            if (!config.DEBUG) {
                bot.sendMessage(config.LOG_CHANEL_ID,
                    `⚠️ Error in callback: ${handlerName}\n\nUser: ${userId}\nCallback: ${call?.data}\nError: ${error.message}`
                ).catch(e => log.error('Failed to send error notification', { stack: e.stack }));
            }
            try {
                if (call?.id) {
                    await bot.answerCallbackQuery(call.id, {
                        text: 'Произошла ошибка. Попробуйте еще раз.',
                        show_alert: true
                    });
                }
            } catch (e) {
                log.error('Failed to answer callback query', { stack: e.stack });
            }
        }
    };
}
