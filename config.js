import 'dotenv/config'

const config = {
    // === Server ===
    PORT: process.env.PORT || 5000,

    // === Database ===
    DB_URI: process.env.DB_URI,

    // === JWT ===
    JWT_ACCESS_SECRET: process.env.JWT_ACCESS_SECRET,
    JWT_REFRESH_SECRET: process.env.JWT_REFRESH_SECRET,

    // === KSU (University) ===
    KSU_DOMAIN: process.env.KSU_DOMAIN || 'https://schedule.buketov.edu.kz',
    KSU_LOGIN: process.env.KSU_LOGIN?.trim(),
    KSU_PASSWORD: process.env.KSU_PASSWORD?.trim(),

    // === Telegram Bot ===
    TG_TOKEN: process.env.TG_TOKEN,
    LOG_CHANEL_ID: process.env.LOG_CHANEL_ID,
    LOGGER_TG_TOKEN: process.env.LOGGER_TG_TOKEN,
    BOT_ID: process.env.BOT_ID,
    BOT_MODE: process.env.BOT_MODE?.trim() || 'polling',
    WEBHOOK_DOMAIN: process.env.WEBHOOK_DOMAIN?.trim(),
    WEBHOOK_PATH: process.env.WEBHOOK_PATH?.trim() || '/bot/webhook',

    // === Debug ===
    DEBUG: process.env.DEBUG === "true",

    // === Browser/Proxy ===
    START_BROWSER: process.env.START_BROWSER === "true",
    AUTO_KSU_AUTH: process.env.AUTO_KSU_AUTH?.trim() === "true",
    USE_PROXY: process.env.USE_PROXY === "true",
    HTTP_PROXY: process.env.HTTP_PROXY,
    PROXY_LOGIN: process.env.PROXY_LOGIN,
    PROXY_PASSWORD: process.env.PROXY_PASSWORD,
    USE_FREE_PROXIES: process.env.USE_FREE_PROXIES?.trim() === "true",

    // === OpenAI (GPT) ===
    TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN,
}

// Validate critical variables
const criticalVars = ['DB_URI', 'TG_TOKEN', 'KSU_LOGIN', 'KSU_PASSWORD', 'LOG_CHANEL_ID', 'LOGGER_TG_TOKEN', 'BOT_ID'];
criticalVars.forEach(key => {
    if (config[key] === undefined) {
        console.warn(`[WARNING] Missing critical environment variable: ${key}`);
    }
});

if (config.BOT_MODE === 'webhook') {
    if (!config.WEBHOOK_DOMAIN) {
        throw new Error('WEBHOOK_DOMAIN is required when BOT_MODE=webhook');
    }
}

export default config
