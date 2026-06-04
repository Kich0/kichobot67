import 'dotenv/config'

const config = {
    JWT_ACCESS_SECRET: process.env.JWT_ACCESS_SECRET,
    JWT_REFRESH_SECRET: process.env.JWT_REFRESH_SECRET,
    PORT: process.env.PORT,
    DB_URI: process.env.DB_URI,
    DEBUG: process.env.DEBUG === "true",
    TG_TOKEN: process.env.TG_TOKEN,
    KSU_DOMAIN: process.env.KSU_DOMAIN,
    KSU_LOGIN:process.env.KSU_LOGIN,
    KSU_PASSWORD:process.env.KSU_PASSWORD,
    LOG_CHANEL_ID:process.env.LOG_CHANEL_ID,
    LOGGER_TG_TOKEN:process.env.LOGGER_TG_TOKEN,
    HTTP_PROXY:process.env.HTTP_PROXY,
    PROXY_LOGIN:process.env.PROXY_LOGIN,
    PROXY_PASSWORD:process.env.PROXY_PASSWORD,
    USE_PROXY: process.env.USE_PROXY === "true",
    START_BROWSER: process.env.START_BROWSER === "true",
    AUTO_KSU_AUTH: process.env.AUTO_KSU_AUTH === "true"
}

if(Object.values(config).some((value => value === undefined))){
    console.error('ЗАПОЛНИ .env дурень. в конфиге undefined')
    console.error('ЗАПОЛНИ .env дурень. в конфиге undefined')
    console.error('ЗАПОЛНИ .env дурень. в конфиге undefined')
}

export default config