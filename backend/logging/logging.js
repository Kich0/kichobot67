import "dotenv/config";
import "winston-mongodb";
import DailyRotateFile from "winston-daily-rotate-file";
import {createLogger, transports, format} from "winston";
import CustomTransport from "./customTransport.js";
import config from "../config.js";

const log = createLogger({
    transports: [
        // Консоль: выводит всё (отслеживается в Render Logs Dashboard)
        new transports.Console({
            level: 'silly',
            format: format.combine(
                format.timestamp({format: 'YYYY-MM-DD HH:mm:ss'}),
                format.printf(({level, message, stack, timestamp}) => {
                    return `${timestamp} || ${level.toUpperCase()} || ${message}\n${stack || ''}`;
                }),
            ),
        }),

        // На диск пишем только критические ошибки и предупреждения
        new transports.File({
            filename: "error_logs.log",
            level: 'error',
            format: format.combine(format.timestamp(), format.json())
        }),
        new DailyRotateFile({
            level: 'warn',
            format: format.combine(format.timestamp(), format.json()),
            filename: 'logs/%DATE%.log',
            datePattern: 'DD.MM.YYYY',
            zippedArchive: true,
            maxSize: '10m',
            maxFiles: '14d'
        })
    ],
});

if (!config.DEBUG) {
    log.add(new CustomTransport({
        level: "warn"
    })); // telegram warning notifications
}

export default log;
