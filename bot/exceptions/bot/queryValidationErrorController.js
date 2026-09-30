import log from "../../logging/logging.js";
import {startCommandController} from "../../controllers/commands/startCommandController.js";

export async function queryValidationErrorController(arg1, arg2) {
    const call = arg2 || arg1;
    try {
        const chatId = call?.message?.chat?.id;
        log.error(`User ${chatId} used an incorrect callback: ${call?.data}. Данные об ошибке в метаданных. Делаю редирект на главную`, {
            call
        })
        if (call?.message) {
            await startCommandController(call.message)
        }
    } catch (e) {
        log.error("УЛЬТРА МЕГА ВАЖНО! ОШИБКА ПРИ ПОПЫТКЕ ОБРАБОТАТЬ ОШИБКУ! queryValidationErrorController." + e.message,
            {call, stack: e.stack})
    }
}