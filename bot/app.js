import TelegramBot from "node-telegram-bot-api"
import express from "express"
import cors from "cors"
import mongoose from "mongoose"
import log from "./logging/logging.js"
import db from "./db/connection.js"
import config from "./config.js"
import {setupCommandHandlers} from "./handlers/commandHandler.js";
import setupCallbackHandlers from "./handlers/callbackHandler.js";
import setupAdminCommandHandler from "./handlers/adminCommandHandler.js";
import errorMiddleware from "./middlewares/errorMiddleware.js";
import router from "./router.js";
import setupDocumentHandler from "./handlers/documentHandler.js";
import {setupUserDailyStatisticsLogging} from "./cron/userDailyStatisticsLogging.js";
import {setupDailyDataUpdate} from "./cron/dailyDataUpdate.js";
import {setupLoggingPathUpdate} from "./cron/loggingPathUpdate.js";
import setupNewChatMemberHandler from "./handlers/newChatMemberHandler.js";
import {setupAnyMessageHandler} from "./handlers/anyMessageHandler.js";
import {i18nextInit} from "./locales/init.js";
import botHealthMonitor from "./utils/botHealthMonitor.js";
import WebhookRetryManager from "./utils/webhookRetry.js";
let botOptions = {};
if (config.BOT_MODE === 'webhook') {
    botOptions = { polling: false, webHook: false };
    log.info(`Bot will run in WEBHOOK mode. Webhook URL will be set after server starts.`);
} else {
    botOptions = { polling: { autoStart: true } };
    log.info(`Bot is running in POLLING mode.`);
}

export const bot = new TelegramBot(config.TG_TOKEN, botOptions);
let webhookRetryManager = null;
if (config.BOT_MODE === 'webhook') {
    webhookRetryManager = new WebhookRetryManager(bot);
}
bot.on('polling_error', (error) => {
    log.error('Polling error occurred!', {
        error: error.message,
        code: error.code,
        stack: error.stack
    });
    if (!config.DEBUG) {
        bot.sendMessage(config.LOG_CHANEL_ID,
            `⚠️ POLLING ERROR!\n\nError: ${error.message}\nCode: ${error.code}\n\nBot may have stopped receiving updates!`
        ).catch(e => log.error('Failed to send polling error notification', { stack: e.stack }));
    }
});

bot.on('webhook_error', (error) => {
    log.error('Webhook error occurred!', {
        error: error.message,
        stack: error.stack
    });
});
bot.on('message', () => botHealthMonitor.updateActivity());
bot.on('callback_query', () => botHealthMonitor.updateActivity());

const app = express();
app.use(cors());
app.use(express.json());
app.use("/bot", router);
app.use(errorMiddleware)

const port = process.env.PORT || 5001;
app.get('/', (req, res) => res.send('Bot is working!'));
const server = app.listen(port, async () => {
    log.info(`Kichobot bot started at ${port} port.`);
    log.info(`Kichobot bot started at ${port} port.`);
    if (config.BOT_MODE === 'webhook' && webhookRetryManager) {
        const webhookUrl = `${config.WEBHOOK_DOMAIN}${config.WEBHOOK_PATH}`;
        await webhookRetryManager.setWebhookWithRetry(webhookUrl);
        setInterval(async () => {
            await webhookRetryManager.monitorWebhookHealth();
        }, 5 * 60 * 1000);
    }
});

export const userLastRequest = {};
export const userWarningSent = {};

(async () => {
    await db.connect(config.DB_URI)
        .then(() => {
            log.info("Успешное подключение к базе данных. БОТ РАБОТАЕТ КОРРЕКТНО ПО ИДЕЕ!")
        })
        .catch((e) => {
            log.error("Ошибка подключения к базе данных! ВЫЗЫВАЮ ФИКСИКОВ ВИУ ВИУ ВИУ!", {stack: e.stack})
        })

    await i18nextInit();

    await setupCommandHandlers();
    await setupAdminCommandHandler();
    await setupCallbackHandlers();
    await setupDocumentHandler()
    await setupNewChatMemberHandler()
    await setupAnyMessageHandler();
    await bot.setMyCommands([
        { command: '/start', description: 'Меню / Мәзір' }
    ]);
    await bot.setMyDescription({
        description: 'Kicho - расписание КарГУ Букетов 📚'
    }).catch(e => log.error('Failed to set bot description', { stack: e.stack }));
    await bot.setMyShortDescription({
        short_description: 'Kicho - расписание КарГУ Букетов 📚'
    }).catch(e => log.error('Failed to set bot short description', { stack: e.stack }));

    await setupUserDailyStatisticsLogging()
    await setupDailyDataUpdate()
    await setupLoggingPathUpdate()
})().catch(async (e) => {
    console.error(e)
    await bot.sendMessage(config.LOG_CHANEL_ID, "Прозошла какая то лютая ошибка. Сработал кетч из апп.жс. Данные об ошибке в логах pm2 будут.")
});
process.on('uncaughtException', async (error) => {
    log.error('Uncaught Exception!', {
        error: error.message,
        stack: error.stack
    });

    if (!config.DEBUG) {
        await bot.sendMessage(config.LOG_CHANEL_ID,
            `🚨 UNCAUGHT EXCEPTION!\n\nError: ${error.message}\n\nBot is still running but this needs attention!`
        ).catch(e => log.error('Failed to send uncaught exception notification', { stack: e.stack }));
    }
});

process.on('unhandledRejection', async (reason, promise) => {
    log.error('Unhandled Rejection!', {
        reason: reason,
        promise: promise
    });

    if (!config.DEBUG) {
        await bot.sendMessage(config.LOG_CHANEL_ID,
            `🚨 UNHANDLED REJECTION!\n\nReason: ${reason}\n\nBot is still running but this needs attention!`
        ).catch(e => log.error('Failed to send unhandled rejection notification', { stack: e.stack }));
    }
});
const gracefulShutdown = async (signal) => {
    log.info(`${signal} received. Starting graceful shutdown...`);

    try {
        server.close(() => {
            log.info('HTTP server closed');
        });
        if (config.BOT_MODE === 'webhook') {
            await bot.deleteWebHook();
            log.info('Webhook removed');
        } else {
            await bot.stopPolling();
            log.info('Polling stopped');
        }
        if (mongoose.connection.readyState !== 0) {
            await mongoose.connection.close();
            log.info('Database connection closed');
        }
        if (webhookRetryManager) {
            webhookRetryManager.cancelRetry();
            log.info('Webhook retry manager stopped');
        }

        log.info('Graceful shutdown completed');
        process.exit(0);
    } catch (e) {
        log.error('Error during graceful shutdown', { stack: e.stack });
        process.exit(1);
    }
};

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));



