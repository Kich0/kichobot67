/**
 * SHARED BOT INSTANCE
 * 
 * Единственный экземпляр TelegramBot, который используется всеми модулями.
 * Создаётся здесь и импортируется из server.js и bot/app.js.
 * 
 * Это решает проблему циклических зависимостей:
 * server.js → bot/handlers → bot/app.js → server.js (ЦИКЛИЧЕСКАЯ!)
 * 
 * Теперь:
 * botInstance.js ← server.js
 * botInstance.js ← bot/app.js
 * botInstance.js ← bot/handlers (через bot/app.js)
 */

import TelegramBot from "node-telegram-bot-api";
import config from "./config.js";

let botOptions = {};
const isTestMode = process.env.NODE_ENV === 'test' ||
    process.execArgv.includes('--test') ||
    process.argv.some(arg => typeof arg === 'string' && arg.includes('test'));

if (config.BOT_MODE === 'webhook' || isTestMode) {
    botOptions = { polling: false, webHook: false };
} else {
    botOptions = { polling: { autoStart: true } };
}

export const bot = new TelegramBot(config.TG_TOKEN, botOptions);
export const userLastRequest = {};
export const userWarningSent = {};
