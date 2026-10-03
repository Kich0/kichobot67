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

export class UserActionService {
    constructor() {
        this.queue = [];
        this.maxQueueSize = 2000;
        this.batchThreshold = 25;
        this.flushIntervalMs = 1000;
        this.isFlushing = false;

        this.timer = setInterval(() => {
            this.flush().catch(err => {
                log.error("[UserActionService] Ошибка авто-сброса очереди действий: " + err.message);
            });
        }, this.flushIntervalMs);

        if (this.timer && typeof this.timer.unref === 'function') {
            this.timer.unref();
        }
    }

    stop() {
        if (this.timer) {
            clearInterval(this.timer);
            this.timer = null;
        }
    }

    async logAction(userId, username, action, text, { entityId = null, entityName = null } = {}) {
        try {
            const numUserId = Number(userId);
            if (!userId || isNaN(numUserId)) return;
            const cleanUsername = username ? String(username).replace(/^@/, '') : null;
            const safeAction = String(action || 'action').trim() || 'action';
            // Гарантируем непустой текст, чтобы схема Mongoose ({ required: true }) не падала с ValidationError
            const safeText = formatCappedText(text) || safeAction;

            const record = {
                userId: numUserId,
                username: cleanUsername,
                action: safeAction,
                text: safeText,
                entityId: entityId !== null && entityId !== undefined ? Number(entityId) : null,
                entityName: entityName ? String(entityName) : null,
                createdAt: new Date()
            };

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
        const day = String(d.getDate()).padStart(2, '0');
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const hours = String(d.getHours()).padStart(2, '0');
        const mins = String(d.getMinutes()).padStart(2, '0');
        const secs = String(d.getSeconds()).padStart(2, '0');
        return `${day}.${month}.${d.getFullYear()} ${hours}:${mins}:${secs}`;
    }

    formatActionsList(actions) {
        if (!actions || actions.length === 0) {
            return '• <i>История действий пуста (пока нет записей)</i>';
        }

        return actions.map(a => {
            const time = this.formatDate(a.createdAt);
            return `• <code>${time}</code> — ${a.text}`;
        }).join('\n');
    }
}

export default new UserActionService();
