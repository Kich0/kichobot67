import { UserAction } from "../models/userAction.js";
import log from "../logging/logging.js";

class UserActionService {
    async logAction(userId, username, action, text, { entityId = null, entityName = null } = {}) {
        try {
            if (!userId) return;
            const cleanUsername = username ? username.replace(/^@/, '') : null;
            await UserAction.create({
                userId,
                username: cleanUsername,
                action,
                text,
                entityId,
                entityName
            });
        } catch (e) {
            log.error(`[UserActionService] Ошибка логирования действия для ${userId}: ` + e.message);
        }
    }

    async getUserActions(userId, limit = 15) {
        try {
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
