import mongoose from "mongoose";
import { BlackList } from "../models/blackList.js";
import log from "../logging/logging.js";

class BlackListService {
    restrictions = new Map(); // userId -> { type, until, reason, username, bannedBy, createdAt }

    constructor() {
        this.loadFromDb().catch(() => {});

        // Периодическая очистка истекших ограничений каждые 3 минуты
        setInterval(() => {
            const now = Date.now();
            for (const [uid, item] of this.restrictions.entries()) {
                if (item.until && now > new Date(item.until).getTime()) {
                    this.restrictions.delete(uid);
                    if (mongoose.connection?.readyState === 1) {
                        BlackList.deleteOne({ userId: uid }).catch(() => {});
                    }
                }
            }
        }, 3 * 60 * 1000).unref();
    }

    async loadFromDb() {
        if (mongoose.connection?.readyState !== 1) return;
        try {
            const list = await BlackList.find({}).lean();
            const now = Date.now();
            for (const item of list) {
                if (item.until && now > new Date(item.until).getTime()) {
                    BlackList.deleteOne({ userId: item.userId }).catch(() => {});
                    continue;
                }
                this.restrictions.set(Number(item.userId), {
                    userId: Number(item.userId),
                    type: item.type || 'ban',
                    until: item.until ? new Date(item.until) : null,
                    reason: item.reason || null,
                    username: item.username || null,
                    bannedBy: item.bannedBy || null,
                    createdAt: item.createdAt || new Date(),
                });
            }
        } catch (e) {
            // MongoDB may not be connected in unit tests
        }
    }

    getRestriction(userId) {
        if (!userId) return null;
        const numId = Number(userId);
        const item = this.restrictions.get(numId);
        if (!item) return null;

        // Проверка истечения времени
        if (item.until && Date.now() > new Date(item.until).getTime()) {
            this.restrictions.delete(numId);
            BlackList.deleteOne({ userId: numId }).catch(() => {});
            return null;
        }

        return item;
    }

    isBanned(userId) {
        const item = this.getRestriction(userId);
        return item ? item.type === 'ban' : false;
    }

    isMuted(userId) {
        const item = this.getRestriction(userId);
        return item ? item.type === 'mute' : false;
    }

    async isBlackListed(userId) {
        return !!this.getRestriction(userId);
    }

    async addBan(userId, { until = null, reason = null, username = null, bannedBy = null } = {}) {
        const numId = Number(userId);
        const data = {
            userId: numId,
            type: 'ban',
            until: until ? new Date(until) : null,
            reason: reason ? String(reason).trim() : null,
            username: username ? String(username).replace(/^@/, '') : null,
            bannedBy: bannedBy ? Number(bannedBy) : null,
            createdAt: new Date(),
        };

        this.restrictions.set(numId, data);

        if (mongoose.connection?.readyState === 1) {
            try {
                await BlackList.findOneAndUpdate(
                    { userId: numId },
                    { $set: data },
                    { upsert: true, new: true }
                );
            } catch (e) {
                log.error(`[BlackList] Ошибка сохранения бана в БД: ${e.message}`);
            }
        }

        return data;
    }

    async addMute(userId, { until = null, reason = null, username = null, bannedBy = null } = {}) {
        const numId = Number(userId);
        const data = {
            userId: numId,
            type: 'mute',
            until: until ? new Date(until) : null,
            reason: reason ? String(reason).trim() : null,
            username: username ? String(username).replace(/^@/, '') : null,
            bannedBy: bannedBy ? Number(bannedBy) : null,
            createdAt: new Date(),
        };

        this.restrictions.set(numId, data);

        if (mongoose.connection?.readyState === 1) {
            try {
                await BlackList.findOneAndUpdate(
                    { userId: numId },
                    { $set: data },
                    { upsert: true, new: true }
                );
            } catch (e) {
                log.error(`[BlackList] Ошибка сохранения мута в БД: ${e.message}`);
            }
        }

        return data;
    }

    async remove(userId) {
        const numId = Number(userId);
        this.restrictions.delete(numId);

        if (mongoose.connection?.readyState === 1) {
            try {
                await BlackList.deleteOne({ userId: numId });
            } catch (e) {
                log.error(`[BlackList] Ошибка удаления из БД: ${e.message}`);
            }
        }
    }

    async addToBlackList(userId) {
        return this.addBan(userId);
    }

    async getAll() {
        return Array.from(this.restrictions.values());
    }

    getAllRestrictions() {
        return Array.from(this.restrictions.values());
    }
}

export default new BlackListService();