import Transport from 'winston-transport';
import https from "https";
import config from "../config.js";
import path from "path";
import { URL } from 'url';

const __dirname = new URL('.', import.meta.url).pathname;

class CustomTransport extends Transport {
    constructor(options) {
        super(options);
    }

    log(info, callback) {
        try {
            const BASE_DIR = path.basename(path.dirname(__dirname));
            const log_chanel_id = config.LOG_CHANEL_ID;
            const token = config.LOGGER_TG_TOKEN;

            if (!token || !log_chanel_id) {
                return callback();
            }

            const msgStr = typeof info.message === 'string' ? info.message : JSON.stringify(info.message);
            const rawText = `[${BASE_DIR}][${info.level}] ${msgStr}`.slice(0, 4000);
            const text = encodeURIComponent(rawText);

            const url = `https://api.telegram.org/bot${token}/sendMessage?chat_id=${log_chanel_id}&text=${text}`;
            https.get(url).on('error', () => {});
        } catch (e) {
            console.error("ОШИБКА ПРИ ПОПЫТКЕ ОТОСЛАТЬ ЛОГ В ТЕЛЕГРАМ:", e.message);
        } finally {
            callback();
        }
    }
}

export default CustomTransport;