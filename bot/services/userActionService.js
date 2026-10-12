import { UserAction } from "../models/userAction.js";
import log from "../logging/logging.js";

export function formatCappedText(rawText) {
    if (!rawText) return '';
    let text = String(rawText).trim();
    if (!text) return '';
    const words = text.split(/\s+/);
    if (words.length > 50) {
        text = words.slice(0, 50).join(' ');
    }
    if (text.length > 250) {
        text = text.slice(0, 250);
    }
    return text;
}

export function deduplicateActions(allCandidates) {
    const seenIds = new Set();
    const seenCompositeKeys = new Set();
    const actions = [];
    let duplicatesSkipped = 0;

    for (const a of allCandidates) {
        if (!a) continue;

        // Уровень 1: дедупликация по уникальному идентификатору _id MongoDB
        const idStr = a._id ? String(a._id) : null;
        if (idStr && seenIds.has(idStr)) {
            duplicatesSkipped++;
            continue;
        }

        // Уровень 2: дедупликация по составному ключу (userId + секундная отсечка + действие + текст)
        const dateMs = a.createdAt ? new Date(a.createdAt).getTime() : 0;
        const timeSec = !isNaN(dateMs) && dateMs > 0 ? Math.floor(dateMs / 1000) : 0;
        const compositeKey = `${a.userId}::${timeSec}::${a.action}::${a.text}`;
        if (seenCompositeKeys.has(compositeKey)) {
            duplicatesSkipped++;
            continue;
        }

        if (idStr) seenIds.add(idStr);
        seenCompositeKeys.add(compositeKey);
        actions.push(a);
    }

    // Хронологическая сортировка: от самых новых к самым старым (с защитой от NaN)
    actions.sort((a, b) => ((new Date(b.createdAt).getTime() || 0) - (new Date(a.createdAt).getTime() || 0)));

    return { actions, duplicatesSkipped };
}

export function styleHeaderRow(sheet) {
    if (!sheet) return;
    const row = sheet.getRow(1);
    row.font = { bold: true, color: { argb: "FFFFFFFF" }, size: 11 };
    row.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF1A73E8" } };
    row.alignment = { vertical: "middle", horizontal: "center" };
    row.height = 25;

    // Закрепление первой строки (заголовка)
    sheet.views = [
        { state: 'frozen', xSplit: 0, ySplit: 1, activeCell: 'A2' }
    ];

    // Включение AutoFilter по всей ширине колонок
    if (sheet.columns && sheet.columns.length > 0) {
        sheet.autoFilter = {
            from: { row: 1, column: 1 },
            to: { row: 1, column: sheet.columns.length }
        };
    }
}

export class UserActionService {
    constructor() {
        this.queue = [];
        this.maxQueueSize = 2000;
        this.batchThreshold = 25;
        this.flushIntervalMs = 1000;
        this.isFlushing = false;

        // In-memory кэш пользователей, совершавших действия (O(1) фильтр)
        this.knownActiveUsers = new Set();
        // In-memory кэш пользователей, нажимавших кнопки
        this.knownButtonUsers = new Set();

        this.timer = setInterval(() => {
            this.flush().catch(err => {
                log.error("[UserActionService] Ошибка авто-сброса очереди действий: " + err.message);
            });
        }, this.flushIntervalMs);

        if (this.timer && typeof this.timer.unref === 'function') {
            this.timer.unref();
        }
    }

    clearCache() {
        this.knownActiveUsers.clear();
        this.knownButtonUsers.clear();
    }

    stop() {
        if (this.timer) {
            clearInterval(this.timer);
            this.timer = null;
        }
    }

    async _checkHasPriorAction(userId) {
        if (this.queue.some(r => r.userId === userId)) return true;
        try {
            if (typeof UserAction?.exists === 'function') {
                if (UserAction.db?.readyState !== 1 && !UserAction._isMocked) {
                    return false;
                }
                const res = await Promise.race([
                    UserAction.exists({ userId }),
                    new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 250))
                ]);
                return Boolean(res);
            }
        } catch (_) {}
        return false;
    }

    async _checkHasPriorButton(userId) {
        const isBtn = (act) => act && !['command', 'chat_input', 'first_message'].includes(act) && !act.startsWith('media_');
        if (this.queue.some(r => r.userId === userId && isBtn(r.action))) return true;
        try {
            if (typeof UserAction?.exists === 'function') {
                if (UserAction.db?.readyState !== 1 && !UserAction._isMocked) {
                    return false;
                }
                const res = await Promise.race([
                    UserAction.exists({
                        userId,
                        action: { $nin: ['command', 'chat_input', 'first_message', 'media_photo', 'media_sticker', 'media_video', 'media_voice', 'media_document'] }
                    }),
                    new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 250))
                ]);
                return Boolean(res);
            }
        } catch (_) {}
        return false;
    }

    async logAction(userId, username, action, text, { entityId = null, entityName = null } = {}) {
        try {
            const numUserId = Number(userId);
            if (!userId || isNaN(numUserId)) return;
            const cleanUsername = username ? String(username).replace(/^@/, '') : null;
            const safeAction = String(action || 'action').trim() || 'action';
            const rawSafeText = formatCappedText(text) || safeAction;

            const isButtonAction = !['command', 'chat_input', 'first_message'].includes(safeAction)
                && !safeAction.startsWith('media_');

            let isFirstAction = false;
            let isFirstButton = false;

            // 1. Проверка первого действия вообще (сообщение или кнопка)
            if (!this.knownActiveUsers.has(numUserId)) {
                const hasPrior = await this._checkHasPriorAction(numUserId);
                if (!hasPrior) {
                    isFirstAction = true;
                }
                this.knownActiveUsers.add(numUserId);
            }

            // 2. Проверка первого нажатия кнопки
            if (isButtonAction && !this.knownButtonUsers.has(numUserId)) {
                const hasPriorButton = await this._checkHasPriorButton(numUserId);
                if (!hasPriorButton) {
                    isFirstButton = true;
                }
                this.knownButtonUsers.add(numUserId);
            }

            // Формируем текст с префиксом первого действия / кнопки
            let safeText = rawSafeText;
            let isFirst = false;

            if (isFirstAction) {
                isFirst = true;
                if (isButtonAction) {
                    safeText = `🆕 [Первое действие пользователя (кнопка)] ${rawSafeText}`;
                } else {
                    safeText = `🆕 [Первое сообщение пользователя] ${rawSafeText}`;
                }
            } else if (isFirstButton) {
                isFirst = true;
                safeText = `🆕 [Первое нажатие кнопки] ${rawSafeText}`;
            }

            const record = {
                userId: numUserId,
                username: cleanUsername,
                action: safeAction,
                text: safeText,
                isFirst: isFirst,
                entityId: entityId !== null && entityId !== undefined ? Number(entityId) : null,
                entityName: entityName ? String(entityName) : null,
                createdAt: new Date()
            };

            // Если это первое действие или первое нажатие кнопки — отправляем уведомление в логи чата (log.warn -> Telegram LOG_CHANEL_ID)
            if (isFirstAction || isFirstButton) {
                const userTag = cleanUsername ? `@${cleanUsername}` : `ID ${numUserId}`;
                const logLabel = isFirstAction
                    ? (isButtonAction ? '[Первое действие пользователя]' : '[Первое сообщение пользователя]')
                    : '[Первое нажатие кнопки]';

                log.warn(`🆕 ${logLabel} /get_user${numUserId} (${userTag}): ${safeText}`, {
                    userId: numUserId,
                    username: cleanUsername,
                    isFirst: true,
                    action: safeAction
                });
            }

            if (this.queue.length >= this.maxQueueSize) {
                this.queue.shift();
            }
            this.queue.push(record);

            if (this.queue.length >= this.batchThreshold) {
                await this.flush();
            }
        } catch (e) {
            log.error(`[UserActionService] Ошибка логирования действия для ${userId}: ` + e.message);
        }
    }

    async flush() {
        while (this.isFlushing) {
            await new Promise(r => setTimeout(r, 20));
        }
        if (this.queue.length === 0) return;

        this.isFlushing = true;
        const batch = this.queue.splice(0, this.queue.length);
        try {
            await UserAction.insertMany(batch, { ordered: false });
        } catch (e) {
            log.error(`[UserActionService] Ошибка записи батча (${batch.length} записей): ` + e.message);
        } finally {
            this.isFlushing = false;
        }
    }

    async getUserActions(userId, limit = 15) {
        try {
            await this.flush();
            return await UserAction.find({ userId })
                .sort({ createdAt: -1 })
                .limit(limit)
                .lean();
        } catch (e) {
            log.error(`[UserActionService] Ошибка получения логов для ${userId}: ` + e.message);
            return [];
        }
    }

    async getRecentActions(limit = 20) {
        try {
            await this.flush();
            return await UserAction.find({})
                .sort({ createdAt: -1 })
                .limit(limit)
                .lean();
        } catch (e) {
            log.error("[UserActionService] Ошибка получения последних логов: " + e.message);
            return [];
        }
    }

    formatDate(dateObj) {
        if (!dateObj) return '';
        const d = new Date(dateObj);
        if (isNaN(d.getTime())) return '';
        // Гарантированное точное время Казахстана (Asia/Almaty, UTC+5)
        const kzDate = new Date(d.getTime() + 5 * 60 * 60 * 1000);
        const day = String(kzDate.getUTCDate()).padStart(2, '0');
        const month = String(kzDate.getUTCMonth() + 1).padStart(2, '0');
        const year = kzDate.getUTCFullYear();
        const hours = String(kzDate.getUTCHours()).padStart(2, '0');
        const mins = String(kzDate.getUTCMinutes()).padStart(2, '0');
        const secs = String(kzDate.getUTCSeconds()).padStart(2, '0');
        return `${day}.${month}.${year} ${hours}:${mins}:${secs}`;
    }

    formatActionsList(actions) {
        if (!actions || actions.length === 0) {
            return '• <i>История действий пуста (пока нет записей)</i>';
        }

        const escape = (str) => {
            if (!str) return '';
            return String(str)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        };

        return actions.map(a => {
            const time = this.formatDate(a.createdAt);
            const rawText = a.text || '';
            const prefix = (a.isFirst && !rawText.includes('[Первое')) ? '🆕 [Первое действие] ' : '';
            const safeText = escape(`${prefix}${rawText}`);
            return `• <code>${time}</code> — ${safeText}`;
        }).join('\n');
    }
}

export default new UserActionService();
