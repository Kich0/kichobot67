import log from "../../logging/logging.js";
import {bot} from "../../app.js";
import {criticalErrorController} from "../../exceptions/bot/criticalErrorController.js";
import {commandAntiSpamMiddleware} from "../../middlewares/bot/commandAntiSpamMiddleware.js";

const errorCatch = async (e, msg) => {
    log.error(`ВАЖНО!User ${msg.chat.id}! ОШИБКА В newsCommandController. Юзеру сказано что бот прибоел.` + e.message, {
        stack: e.stack,
        userId: msg.chat.id
    })
    await criticalErrorController(msg)
}

export async function newsCommandController(msg) {
    await commandAntiSpamMiddleware(msg, async () => {
        try {
            const msg_text = '👋 Рад что тебе интересны наши обновления!\n' +
                '\n' +

                '🔴 Добавлена возможность поиска расписания по ключевым словам. Подробнее /search \n' +
                '🟡 Добавлена возможность выбора языка интерфейса. /start \n' +
                '🟢 Добавлен полноценный веб интерейс расписания, доступный по кнопочке снизу слева "Тык!". \n' +
                '⚫️ Добавлены навигационные кнопочки при команде /start. Получение расписания вынесено в отдельную кнопочку. \n' +
                '⚪️ Улучшена работа парсера ksu, теперь получение расписания происходит быстрее и стабильнее.\n'
            await bot.sendMessage(msg.chat.id, msg_text)
        } catch (e) {
            await errorCatch(e, msg)
        }
    })
}