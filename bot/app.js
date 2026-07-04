/**
 * СОВМЕСТИМОСТЬ — bot/app.js
 * 
 * Этот файл раньше был точкой входа для бота.
 * Теперь он реэкспортирует бота из botInstance.js.
 * Все модули бота (handlers, controllers) импортируют { bot } отсюда.
 */

export { bot, userLastRequest, userWarningSent } from "../botInstance.js";
