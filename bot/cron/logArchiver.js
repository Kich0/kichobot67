import cron from "node-cron";
import fs from "fs";
import path from "path";
import zlib from "zlib";
import { promisify } from "util";
import { pipeline } from "stream";
import log from "../logging/logging.js";
import config from "../config.js";
import { bot } from "../app.js";

const pipe = promisify(pipeline);

const GOOGLE_DRIVE_CANDIDATE_PATHS = [
    'G:/Мой диск/Kichobot/logs',
    'G:/My Drive/Kichobot/logs',
    process.env.GOOGLE_DRIVE_LOGS_DIR
].filter(Boolean);

/**
 * Упаковка файла в gzip (.gz)
 */
async function compressFile(srcPath, destPath) {
    const gzip = zlib.createGzip({ level: 9 });
    const source = fs.createReadStream(srcPath);
    const destination = fs.createWriteStream(destPath);
    await pipe(source, gzip, destination);
}

/**
 * Ночная архивация логов и выгрузка в облако
 */
export async function archiveAndBackupLogs() {
    try {
        log.info("[LogArchiver] Запуск ночной архивации и выгрузки логов...");
        const logsDir = path.resolve("./logs");

        if (!fs.existsSync(logsDir)) {
            log.info("[LogArchiver] Папка logs отсутствует, нечего архивировать.");
            return;
        }

        const files = fs.readdirSync(logsDir).filter(f => f.endsWith('.log') && !f.startsWith('.'));
        if (files.length === 0) {
            log.info("[LogArchiver] Нет .log файлов для архивации.");
            return;
        }

        const now = new Date();
        const dateStr = `${String(now.getDate()).padStart(2, '0')}-${String(now.getMonth() + 1).padStart(2, '0')}-${now.getFullYear()}_${String(now.getHours()).padStart(2, '0')}-${String(now.getMinutes()).padStart(2, '0')}`;

        // 1. Создаём архив для каждого лог-файла
        const archivedFiles = [];
        for (const file of files) {
            const fullPath = path.join(logsDir, file);
            const stat = fs.statSync(fullPath);
            if (stat.size === 0) continue; // Пропускаем пустые

            const archiveName = `${path.basename(file, '.log')}_${dateStr}.log.gz`;
            const archivePath = path.join(logsDir, archiveName);

            await compressFile(fullPath, archivePath);
            archivedFiles.push({ original: fullPath, archive: archivePath, name: archiveName });
        }

        if (archivedFiles.length === 0) {
            log.info("[LogArchiver] Нет непустых логов для отправки.");
            return;
        }

        // 2. Выгрузка на Google Диск (если смонтирован на компьютере)
        let savedToGDrive = false;
        for (const gPath of GOOGLE_DRIVE_CANDIDATE_PATHS) {
            try {
                if (fs.existsSync(gPath)) {
                    for (const item of archivedFiles) {
                        const targetPath = path.join(gPath, item.name);
                        fs.copyFileSync(item.archive, targetPath);
                    }
                    log.info(`[LogArchiver] ✅ Успешно скопировано на Google Диск: ${gPath}`);
                    savedToGDrive = true;
                    break;
                }
            } catch (err) {
                log.warn(`[LogArchiver] Не удалось записать на Google Диск (${gPath}): ${err.message}`);
            }
        }

        // 3. Выгрузка в Telegram Cloud (для облачного сервера Render)
        const targetChatId = config.LOG_CHANEL_ID || config.ADMIN_ID;
        if (targetChatId && bot) {
            for (const item of archivedFiles) {
                try {
                    await bot.sendDocument(targetChatId, item.archive, {
                        caption: `📁 <b>Архив логов Kichobot</b>\n📅 Дата: <code>${dateStr}</code>\n💾 Файл: <code>${item.name}</code>\n☁️ Google Диск: ${savedToGDrive ? 'Синхронизировано ✅' : 'TG Cloud ✅'}`,
                        parse_mode: 'HTML'
                    });
                    log.info(`[LogArchiver] ✅ Архив ${item.name} отправлен в Telegram (${targetChatId})`);
                } catch (tgErr) {
                    log.warn(`[LogArchiver] Ошибка отправки архива в Telegram: ${tgErr.message}`);
                }
            }
        }

        // 4. Очистка локальных временных архивов и старых логов на сервере Render
        for (const item of archivedFiles) {
            try {
                // Удаляем временный архив из logs/
                if (fs.existsSync(item.archive)) {
                    fs.unlinkSync(item.archive);
                }
                // Очищаем содержимое оригинального лог-файла (truncate), чтобы не забивать диск
                fs.writeFileSync(item.original, '');
            } catch (cleanErr) {
                log.warn(`[LogArchiver] Ошибка очистки лога ${item.original}: ${cleanErr.message}`);
            }
        }

        log.info("[LogArchiver] Архивация и очистка успешно завершены. Диск свободен.");
    } catch (e) {
        log.error("[LogArchiver] Критическая ошибка архиватора логов: " + e.message, { stack: e.stack });
    }
}

/**
 * Инициализация ночного расписания архивации (03:00 по времени Казахстана)
 */
export function setupLogArchiver() {
    cron.schedule('0 3 * * *', async () => {
        await archiveAndBackupLogs();
    }, {
        timezone: "Asia/Almaty"
    });
    log.info("[LogArchiver] Ночная архивация логов настроена на 03:00 KZ (Asia/Almaty).");
}
