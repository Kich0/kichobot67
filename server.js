/**
 * UNIFIED SERVER — kichobot67
 * 
 * Единая точка входа для backend (Express API + Puppeteer) и bot (Telegram).
 * Вместо двух серверов на Render — один процесс, один порт, один лимит.
 * 
 * Бот вызывает сервисы бэкенда НАПРЯМУЮ через import (без HTTP).
 */

import express from "express";
import dns from "dns";
import cors from "cors";
import cookieParser from "cookie-parser";
import mongoose from "mongoose";
import bodyParser from "body-parser";

import config from "./config.js";

// === Backend imports ===
import backendDb from "./backend/db/connection.js";
import backendRouter from "./backend/router.js";
import backendErrorMiddleware from "./backend/middlewares/errorMiddleware.js";
import backendLog from "./backend/logging/logging.js";
import { setupLoggingPathUpdate as setupBackendLoggingPathUpdate } from "./backend/cron/loggingPathUpdate.js";
import { setupKsuReAuth } from "./backend/cron/ksuReAuth.js";
import FreeProxyService from "./backend/services/FreeProxyService.js";

// === Bot imports ===
import TelegramBot from "node-telegram-bot-api";
import botLog from "./bot/logging/logging.js";
import botRouter from "./bot/router.js";
import botErrorMiddleware from "./bot/middlewares/errorMiddleware.js";
import { setupCommandHandlers } from "./bot/handlers/commandHandler.js";
import setupCallbackHandlers from "./bot/handlers/callbackHandler.js";
import setupAdminCommandHandler from "./bot/handlers/adminCommandHandler.js";
import setupDocumentHandler from "./bot/handlers/documentHandler.js";
import { setupUserDailyStatisticsLogging } from "./bot/cron/userDailyStatisticsLogging.js";
import { setupDailyDataUpdate } from "./bot/cron/dailyDataUpdate.js";
import { setupLoggingPathUpdate as setupBotLoggingPathUpdate } from "./bot/cron/loggingPathUpdate.js";
import { setupScheduleCacheWarmup } from "./bot/cron/scheduleCacheWarmup.js";
import setupNewChatMemberHandler from "./bot/handlers/newChatMemberHandler.js";
import { setupAnyMessageHandler } from "./bot/handlers/anyMessageHandler.js";
import { i18nextInit } from "./bot/locales/init.js";
import botHealthMonitor from "./bot/utils/botHealthMonitor.js";
import WebhookRetryManager from "./bot/utils/webhookRetry.js";
import { processMessageGate } from "./bot/middlewares/bot/messageGateMiddleware.js";

// =============================================
// 1. TELEGRAM BOT SETUP (из shared модуля)
// =============================================

import { bot, userLastRequest, userWarningSent } from "./botInstance.js";

backendLog.info(`Bot is running in ${config.BOT_MODE.toUpperCase()} mode.`);

// Экспортируем для совместимости (если кто-то импортирует из server.js)
export { bot, userLastRequest, userWarningSent };

let webhookRetryManager = null;
if (config.BOT_MODE === 'webhook') {
    webhookRetryManager = new WebhookRetryManager(bot);
}

bot.on('polling_error', (error) => {
    backendLog.error('Polling error occurred!', {
        error: error.message,
        code: error.code,
        stack: error.stack
    });
    if (!config.DEBUG) {
        bot.sendMessage(config.LOG_CHANEL_ID,
            `⚠️ POLLING ERROR!\n\nError: ${error.message}\nCode: ${error.code}`
        ).catch(e => backendLog.error('Failed to send polling error notification', { stack: e.stack }));
    }
});

bot.on('webhook_error', (error) => {
    backendLog.error('Webhook error occurred!', {
        error: error.message,
        stack: error.stack
    });
});

bot.on('message', (msg) => processMessageGate(msg, bot));
bot.on('message', () => botHealthMonitor.updateActivity());
bot.on('callback_query', () => botHealthMonitor.updateActivity());

// =============================================
// 2. EXPRESS SERVER SETUP (UNIFIED)
// =============================================

const app = express();

app.use(cors({ origin: '*', optionsSuccessStatus: 200 }));
app.use(express.json({ limit: '5mb' }));
app.use(cookieParser());
app.use(bodyParser.text({ type: 'text/html', limit: '5mb' }));

// Логирование запросов
app.use((req, res, next) => {
    const decodedUrl = decodeURIComponent(req.url);
    backendLog.info(`${req.method} ${decodedUrl}`);
    next();
});

// === Backend routes (парсинг, Puppeteer, расписание) ===
app.use('/express/api', backendRouter);

// === Bot routes (webhook, health, WebApp API) ===
app.use('/bot', botRouter);

// === Health check для Render ===
app.get('/', (req, res) => res.send('Kichobot is alive! 🤖'));

// Error middlewares
app.use(backendErrorMiddleware);

// =============================================
// 3. STARTUP
// =============================================

const appStart = async () => {
    try {
        // Единое подключение к MongoDB
        await backendDb.connect(config.DB_URI);
        backendLog.info(`[Database] Единое подключение к MongoDB установлено`);

        // Запуск Express сервера на одном порту
        const port = config.PORT;
        const server = app.listen(port, async () => {
            backendLog.info(`🚀 Kichobot UNIFIED server started on port ${port}`);
            backendLog.info(`   Backend API: http://localhost:${port}/express/api/`);
            backendLog.info(`   Bot routes:  http://localhost:${port}/bot/`);

            // Настройка webhook если нужно
            if (config.BOT_MODE === 'webhook' && webhookRetryManager) {
                const webhookUrl = `${config.WEBHOOK_DOMAIN}${config.WEBHOOK_PATH}`;
                await webhookRetryManager.setWebhookWithRetry(webhookUrl);
                setInterval(async () => {
                    await webhookRetryManager.monitorWebhookHealth();
                }, 5 * 60 * 1000);
            }
        });

        // === Backend cron ===
        await setupBackendLoggingPathUpdate();
        await setupKsuReAuth();

        // === Proxy pool ===
        if (config.USE_FREE_PROXIES) {
            FreeProxyService.initPool().catch(e => backendLog.error("[ProxyPool] Ошибка автопополнения: " + e.message));
        }

        // === Bot initialization ===
        await i18nextInit();
        await setupCommandHandlers();
        await setupAdminCommandHandler();
        await setupCallbackHandlers();
        await setupDocumentHandler();
        await setupNewChatMemberHandler();
        await setupAnyMessageHandler();

        await bot.setMyCommands([
            { command: '/start', description: 'Меню / Мәзір' }
        ]);

        await bot.setMyDescription({
            description: 'Kicho - расписание КарГУ Букетов 📚'
        }).catch(e => backendLog.error('Failed to set bot description', { stack: e.stack }));

        await bot.setMyShortDescription({
            short_description: 'Kicho - расписание КарГУ Букетов 📚'
        }).catch(e => backendLog.error('Failed to set bot short description', { stack: e.stack }));

        // === Bot cron ===
        await setupUserDailyStatisticsLogging();
        await setupDailyDataUpdate();
        await setupBotLoggingPathUpdate();
        setupScheduleCacheWarmup();

        backendLog.info(`✅ Все модули инициализированы успешно`);
        backendLog.info(`USE_FREE_PROXIES: ${config.USE_FREE_PROXIES}`);

        // DNS test
        dns.resolve('schedule.buketov.edu.kz', (err, addresses) => {
            if (err) backendLog.error("DNS TEST FAILED: " + err.message);
            else backendLog.info("DNS TEST SUCCESS: " + addresses.join(', '));
        });

        // === Graceful Shutdown ===
        const gracefulShutdown = async (signal) => {
            backendLog.info(`${signal} received. Starting graceful shutdown...`);
            try {
                server.close(() => backendLog.info('HTTP server closed'));

                if (config.BOT_MODE === 'webhook') {
                    await bot.deleteWebHook();
                    backendLog.info('Webhook removed');
                } else {
                    await bot.stopPolling();
                    backendLog.info('Polling stopped');
                }

                if (mongoose.connection.readyState !== 0) {
                    await mongoose.connection.close();
                    backendLog.info('Database connection closed');
                }

                if (webhookRetryManager) {
                    webhookRetryManager.cancelRetry();
                    backendLog.info('Webhook retry manager stopped');
                }

                backendLog.info('Graceful shutdown completed');
                process.exit(0);
            } catch (e) {
                backendLog.error('Error during graceful shutdown', { stack: e.stack });
                process.exit(1);
            }
        };

        process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
        process.on('SIGINT', () => gracefulShutdown('SIGINT'));

    } catch (e) {
        backendLog.error("Критическая ошибка при запуске: " + e.message, { stack: e.stack });
        console.error(e);
        process.exit(1);
    }
};

// =============================================
// 4. GLOBAL ERROR HANDLERS
// =============================================

process.on('uncaughtException', async (error) => {
    backendLog.error('Uncaught Exception!', {
        error: error.message,
        stack: error.stack
    });
    if (!config.DEBUG) {
        await bot.sendMessage(config.LOG_CHANEL_ID,
            `🚨 UNCAUGHT EXCEPTION!\n\nError: ${error.message}\n\nBot is still running!`
        ).catch(e => backendLog.error('Failed to send uncaught exception notification', { stack: e.stack }));
    }
});

process.on('unhandledRejection', async (reason, promise) => {
    backendLog.error('Unhandled Rejection!', {
        reason: reason,
        promise: promise
    });
    if (!config.DEBUG) {
        await bot.sendMessage(config.LOG_CHANEL_ID,
            `🚨 UNHANDLED REJECTION!\n\nReason: ${reason}\n\nBot is still running!`
        ).catch(e => backendLog.error('Failed to send unhandled rejection notification', { stack: e.stack }));
    }
});

// =============================================
// 5. START
// =============================================
appStart().catch(e => {
    console.error("Ошибка при запуске unified server: " + e.stack);
    process.exit(1);
});
