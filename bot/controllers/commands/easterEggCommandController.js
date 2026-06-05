import log from "../../logging/logging.js";
import {bot} from "../../app.js";
import {commandAntiSpamMiddleware} from "../../middlewares/bot/commandAntiSpamMiddleware.js";

export async function sixtySevenEasterEggController(msg) {
    await commandAntiSpamMiddleware(msg, async () => {
        try {
            // Создаем огромный текст из "67"
            const spamText = Array(50).fill("67").join(" ");
            
            // Отправляем несколько раз для эффекта "спама", но не слишком много, чтобы не забанил Telegram
            await bot.sendMessage(msg.chat.id, spamText);
            await bot.sendMessage(msg.chat.id, spamText);
            await bot.sendMessage(msg.chat.id, spamText);
        } catch (e) {
            log.error(`Error in sixtySevenEasterEggController: ${e.message}`, {stack: e.stack});
        }
    });
}
