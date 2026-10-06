import { bot } from "../app.js";
import log from "../logging/logging.js";
import userActionService from "../services/userActionService.js";
import userTollService from "../services/userTollService.js";

export default function setupPaymentHandlers() {
    // 1. Предварительное подтверждение платежа Telegram Stars
    bot.on('pre_checkout_query', async (query) => {
        try {
            log.info(`[Payment] Pre-checkout query от user ${query.from?.id} на сумму ${query.total_amount} ${query.currency}`);
            await bot.answerPreCheckoutQuery(query.id, true);
        } catch (e) {
            log.error(`[Payment] Ошибка подтверждения pre_checkout: ${e.message}`, { stack: e.stack });
            await bot.answerPreCheckoutQuery(query.id, false, { error_message: 'Не удалось подтвердить платёж.' }).catch(() => {});
        }
    });

    // 2. Успешный платёж Telegram Stars
    bot.on('message', async (msg) => {
        if (msg.successful_payment) {
            const payment = msg.successful_payment;
            const userId = msg.chat?.id || msg.from?.id;
            const username = msg.from?.username;

            log.info(`[Payment] Успешная оплата от user ${userId}: ${payment.total_amount} ${payment.currency} (charge_id: ${payment.telegram_payment_charge_id})`);

            // Начисляем 1 оплаченное нажатие кнопки
            await userTollService.addPaidCredit(userId, 1);

            userActionService.logAction(
                userId,
                username,
                'stars_payment_success',
                `Оплачено ${payment.total_amount} ⭐ звёзд (ID транзакции: ${payment.telegram_payment_charge_id})`
            ).catch(() => {});

            await bot.sendMessage(
                userId,
                `🌟 <b>Оплата ${payment.total_amount.toLocaleString('ru-RU')} ⭐ звёзд успешно получена!</b>\n\n` +
                `Спасибо за оплату! Нажатие кнопки разблокировано, теперь нажмите её ещё раз.`,
                { parse_mode: 'HTML' }
            ).catch(() => {});
        }
    });
}
