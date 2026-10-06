import mongoose from "mongoose";
import { UserToll } from "../models/userToll.js";
import log from "../logging/logging.js";

class UserTollService {
    tolledUsers = new Map();

    constructor() {
        this.loadFromDb().catch(() => {});
    }

    async loadFromDb() {
        if (mongoose.connection?.readyState !== 1) return;
        try {
            const list = await UserToll.find({ enabled: true }).lean();
            for (const item of list) {
                this.tolledUsers.set(Number(item.userId), {
                    starPrice: item.starPrice || 9000,
                    username: item.username || null,
                    addedBy: item.addedBy || null,
                    createdAt: item.createdAt || new Date(),
                });
            }
        } catch (e) {
            // MongoDB may not be connected in tests or startup
        }
    }

    isUserTolled(userId) {
        if (!userId) return false;
        return this.tolledUsers.has(Number(userId));
    }

    hasPaidCredit(userId) {
        if (!userId) return false;
        const item = this.tolledUsers.get(Number(userId));
        return (item?.paidCredits || 0) > 0;
    }

    consumePaidCredit(userId) {
        const numId = Number(userId);
        const item = this.tolledUsers.get(numId);
        if (item && item.paidCredits > 0) {
            item.paidCredits -= 1;
            if (mongoose.connection?.readyState === 1) {
                UserToll.findOneAndUpdate(
                    { userId: numId },
                    { $inc: { paidCredits: -1 } }
                ).catch(() => {});
            }
            return true;
        }
        return false;
    }

    async addPaidCredit(userId, count = 1) {
        const numId = Number(userId);
        let item = this.tolledUsers.get(numId);
        if (item) {
            item.paidCredits = (item.paidCredits || 0) + count;
        } else {
            item = {
                starPrice: 9000,
                paidCredits: count,
                createdAt: new Date(),
            };
            this.tolledUsers.set(numId, item);
        }

        if (mongoose.connection?.readyState === 1) {
            try {
                await UserToll.findOneAndUpdate(
                    { userId: numId },
                    { $inc: { paidCredits: count } },
                    { upsert: true, new: true }
                );
            } catch (e) {
                log.error(`[UserToll] Ошибка добавления кредита: ${e.message}`);
            }
        }
        return item.paidCredits;
    }

    getTollPrice(userId) {
        if (!userId) return 9000;
        const item = this.tolledUsers.get(Number(userId));
        return item?.starPrice || 9000;
    }

    getTollInfo(userId) {
        if (!userId) return null;
        return this.tolledUsers.get(Number(userId)) || null;
    }

    async enableToll(userId, { starPrice = 9000, username = null, addedBy = null } = {}) {
        const numId = Number(userId);
        const data = {
            starPrice: Number(starPrice) || 9000,
            username: username ? String(username).replace(/^@/, '') : null,
            addedBy: addedBy ? Number(addedBy) : null,
            createdAt: new Date(),
        };

        this.tolledUsers.set(numId, data);

        if (mongoose.connection?.readyState === 1) {
            try {
                await UserToll.findOneAndUpdate(
                    { userId: numId },
                    { $set: { userId: numId, enabled: true, ...data } },
                    { upsert: true, new: true }
                );
            } catch (e) {
                log.error(`[UserToll] Ошибка сохранения в БД: ${e.message}`);
            }
        }

        return data;
    }

    async disableToll(userId) {
        const numId = Number(userId);
        this.tolledUsers.delete(numId);

        if (mongoose.connection?.readyState === 1) {
            try {
                await UserToll.findOneAndUpdate(
                    { userId: numId },
                    { $set: { enabled: false } }
                );
            } catch (e) {
                log.error(`[UserToll] Ошибка отключения в БД: ${e.message}`);
            }
        }
    }

    getAllTolled() {
        const result = [];
        for (const [userId, val] of this.tolledUsers.entries()) {
            result.push({
                userId,
                ...val,
            });
        }
        return result;
    }
}

export default new UserTollService();
