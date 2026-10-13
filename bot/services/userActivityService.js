import {UserActivity} from "../models/user-activity.js";
import {User} from "../models/user.js";
import {UserAction} from "../models/userAction.js";
import userActionService from "./userActionService.js";
import log from "../logging/logging.js";

class UserActivityService {
    getKZDayInfo(daysAgo = 0) {
        const KZ_OFFSET_MS = 5 * 60 * 60 * 1000;
        const nowKz = new Date(Date.now() + KZ_OFFSET_MS);
        const targetKz = new Date(Date.UTC(nowKz.getUTCFullYear(), nowKz.getUTCMonth(), nowKz.getUTCDate() - daysAgo));
        const y = targetKz.getUTCFullYear();
        const m = String(targetKz.getUTCMonth() + 1).padStart(2, '0');
        const d = String(targetKz.getUTCDate()).padStart(2, '0');
        const dateKey = `${y}-${m}-${d}`;
        const dayMonth = `${d}.${m}`;
        const startUtc = new Date(targetKz.getTime() - KZ_OFFSET_MS);
        const endUtc = new Date(targetKz.getTime() + 24 * 60 * 60 * 1000 - 1 - KZ_OFFSET_MS);
        return { dateKey, dayMonth, startUtc, endUtc };
    }

    getKZStartOfDay(daysAgo = 0) {
        return this.getKZDayInfo(daysAgo).startUtc;
    }

    async getComprehensiveStatistics() {
        try {
            if (userActionService && typeof userActionService.flush === 'function') {
                await userActionService.flush();
            }

            const sevenDaysAgo = this.getKZDayInfo(7);
            const sevenDaysAgoStart = sevenDaysAgo.startUtc;

            // 1. Агрегация действий и уникальных активных пользователей в UserAction
            const actionAgg = await UserAction.aggregate([
                {
                    $match: {
                        createdAt: { $gte: sevenDaysAgoStart }
                    }
                },
                {
                    $project: {
                        userId: 1,
                        dateKey: {
                            $dateToString: {
                                format: "%Y-%m-%d",
                                date: { $add: ["$createdAt", 5 * 60 * 60 * 1000] }
                            }
                        }
                    }
                },
                {
                    $group: {
                        _id: "$dateKey",
                        actionsCount: { $sum: 1 },
                        uniqueUsers: { $addToSet: "$userId" }
                    }
                }
            ]);

            // 2. Агрегация регистраций в User
            const userAgg = await User.aggregate([
                {
                    $match: {
                        createdAt: { $gte: sevenDaysAgoStart }
                    }
                },
                {
                    $project: {
                        dateKey: {
                            $dateToString: {
                                format: "%Y-%m-%d",
                                date: { $add: ["$createdAt", 5 * 60 * 60 * 1000] }
                            }
                        }
                    }
                },
                {
                    $group: {
                        _id: "$dateKey",
                        registeredCount: { $sum: 1 }
                    }
                }
            ]);

            const actionMap = new Map();
            const weekUsersSet = new Set();
            let weekTotalActions = 0;

            for (const item of (actionAgg || [])) {
                const dateKey = item._id;
                const users = item.uniqueUsers || [];
                const count = item.actionsCount || 0;
                actionMap.set(dateKey, {
                    actionsCount: count,
                    uniqueUsersCount: users.length
                });
                for (const u of users) {
                    weekUsersSet.add(u);
                }
                weekTotalActions += count;
            }

            const regMap = new Map();
            let weekTotalRegistered = 0;
            for (const item of (userAgg || [])) {
                const dateKey = item._id;
                const count = item.registeredCount || 0;
                regMap.set(dateKey, count);
                weekTotalRegistered += count;
            }

            // Сегодня (daysAgo = 0)
            const todayInfo = this.getKZDayInfo(0);
            const todayActionData = actionMap.get(todayInfo.dateKey) || { actionsCount: 0, uniqueUsersCount: 0 };
            const activeUsersToday = todayActionData.uniqueUsersCount;
            const actionsToday = todayActionData.actionsCount;
            const registeredUsersToday = regMap.get(todayInfo.dateKey) || 0;

            // Прошедшие 7 дней (daysAgo = 1..7)
            const dailyActivity = [];
            const dailyActions = [];
            const dailyRegistrations = [];

            for (let i = 1; i <= 7; i++) {
                const dayInfo = this.getKZDayInfo(i);
                const dayData = actionMap.get(dayInfo.dateKey) || { actionsCount: 0, uniqueUsersCount: 0 };
                const regCount = regMap.get(dayInfo.dateKey) || 0;

                dailyActivity.push({
                    dateStr: dayInfo.dayMonth,
                    count: dayData.uniqueUsersCount
                });
                dailyActions.push({
                    dateStr: dayInfo.dayMonth,
                    count: dayData.actionsCount
                });
                dailyRegistrations.push({
                    dateStr: dayInfo.dayMonth,
                    count: regCount
                });
            }

            return {
                activeUsersToday,
                actionsToday,
                registeredUsersToday,
                weekUniqueUsersCount: weekUsersSet.size,
                weekTotalActionsCount: weekTotalActions,
                weekRegisteredUsersCount: weekTotalRegistered,
                dailyActivity,
                dailyActions,
                dailyRegistrations
            };
        } catch (e) {
            log.error("Ошибка при получении комплексной статистики: " + e.message, { stack: e.stack });
            throw e;
        }
    }

    formatStatMessage(stats) {
        let msg_text = 'Статистика ежедневного онлайна:\n' +
            `Сегодня: ${stats.activeUsersToday}\n`;
        for (const item of (stats.dailyActivity || [])) {
            msg_text += `${item.dateStr}: ${item.count}\n`;
        }
        msg_text += `Всего уникальных пользователей за неделю: ${stats.weekUniqueUsersCount}\n\n`;

        msg_text += 'Статистика ежедневных действий:\n' +
            `Сегодня: ${stats.actionsToday}\n`;
        for (const item of (stats.dailyActions || [])) {
            msg_text += `${item.dateStr}: ${item.count}\n`;
        }
        msg_text += `Всего действий за неделю: ${stats.weekTotalActionsCount}\n\n`;

        msg_text += 'Статистика ежедневных регистраций:\n' +
            `Сегодня: ${stats.registeredUsersToday}\n`;
        for (const item of (stats.dailyRegistrations || [])) {
            msg_text += `${item.dateStr}: ${item.count}\n`;
        }
        msg_text += `Всего зареганных пользователей за неделю: ${stats.weekRegisteredUsersCount}`;

        return msg_text;
    }

    async getTodayUserCount() {
        try {
            if (userActionService && typeof userActionService.flush === 'function') {
                await userActionService.flush();
            }
            const today = this.getKZDayInfo(0);
            const userActionsToday = await UserAction.distinct("userId", {
                createdAt: { $gte: today.startUtc }
            });
            return userActionsToday.length;
        } catch (e) {
            throw new Error("Ошибка при получении кол-ва пользователей за сегодня: " + e.stack);
        }
    }

    async dailyUserActivityLogging() {
        try {
            const userActivity = await this.getTodayUserCount();
            await UserActivity.create({ userActivity });
            log.warn(`Произошла запись активности юзеров: ${userActivity}`);
        } catch (e) {
            throw new Error("Ошибка при логировании дневной активности пользователей: " + e.stack);
        }
    }

    async getWeakUserActivity() {
        try {
            const stats = await this.getComprehensiveStatistics();
            return {
                weakUserActivity: stats.dailyActivity.map(d => ({
                    userActivity: d.count,
                    createdAt: d.dateStr
                })),
                weakUserCount: stats.weekUniqueUsersCount
            };
        } catch (e) {
            throw new Error("Ошибка при получении активности пользователей за неделю: " + e.stack);
        }
    }

    async getWeakUserCount() {
        try {
            const sevenDaysAgo = this.getKZDayInfo(7);
            const weekUsers = await UserAction.distinct("userId", {
                createdAt: { $gte: sevenDaysAgo.startUtc }
            });
            return weekUsers.length;
        } catch (e) {
            throw new Error("Ошибка при получении кол-ва пользователей за неделю: " + e.stack);
        }
    }

    async getUnactiveUsers() {
        try {
            const today = new Date();
            today.setHours(0, 0, 0, 0);

            const oneWeekAgo = new Date(today);
            oneWeekAgo.setDate(today.getDate() - 6);

            return await User.find({
                updatedAt: { $lte: oneWeekAgo }
            });
        } catch (e) {
            throw new Error("Ошибка при получении неактивных пользователей: " + e.stack);
        }
    }

    async getAbsolutelyUnactiveUsers() {
        try {
            const today = new Date();
            today.setHours(0, 0, 0, 0);

            const threeWeeksAgo = new Date(today);
            threeWeeksAgo.setDate(today.getDate() - 21);

            return await User.find({
                updatedAt: { $lte: threeWeeksAgo }
            });
        } catch (e) {
            throw new Error("Ошибка при получении неактивных пользователей: " + e.stack);
        }
    }
}

export default new UserActivityService();