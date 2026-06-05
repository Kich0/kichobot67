import log from "../../logging/logging.js";
import {bot} from "../../app.js";
import {commandAntiSpamMiddleware} from "../../middlewares/bot/commandAntiSpamMiddleware.js";

export async function sixtySevenEasterEggController(msg) {
    await commandAntiSpamMiddleware(msg, async () => {
        try {
            // Отправляем сообщение "67" ровно 5 раз (настоящий спам отдельными сообщениями)
            for (let i = 0; i < 5; i++) {
                await bot.sendMessage(msg.chat.id, "67").catch(e => {
                    log.error(`Failed to send easter egg spam message ${i}: ${e.message}`);
                });
                // Небольшая задержка, чтобы Telegram не забанил нас за превышение лимитов (Too Many Requests)
                await new Promise(resolve => setTimeout(resolve, 100));
            }
        } catch (e) {
            log.error(`Error in sixtySevenEasterEggController: ${e.message}`, {stack: e.stack});
        }
    });
}
