import {userRegistrationStat} from "../models/user-regiistration-stat.js";
import {User} from "../models/user.js";
import log from "../logging/logging.js";
import userActivityService from "./userActivityService.js";

class UserRegistrationStatService {
    getKZStartOfDay(daysAgo = 0) {
        return userActivityService.getKZDayInfo(daysAgo).startUtc;
    }

    async getTodayRegisteredUserCount() {
        try {
            const today = userActivityService.getKZDayInfo(0);
            return await User.countDocuments({ createdAt: { $gte: today.startUtc } });
        } catch (e) {
            throw new Error("Ошибка при получении кол-ва зарегистрированных юзеров сегодня: " + e.stack);
        }
    }

    async dailyRegisteredUserCountLogging() {
        try {
            const registeredUsers = await this.getTodayRegisteredUserCount();
            await userRegistrationStat.create({ registeredUsers });
            log.warn(`Произошла запись зарегистрированных юзеров: ${registeredUsers}`);
        } catch (e) {
            throw new Error("Ошибка при логировании кол-ва зарегистрированных юзеров сегодня: " + e.stack);
        }
    }

    async getWeakRegisteredUserCount() {
        try {
            const sevenDaysAgo = userActivityService.getKZDayInfo(7);
            return await User.countDocuments({
                createdAt: { $gte: sevenDaysAgo.startUtc }
            });
        } catch (e) {
            throw new Error("Ошибка при получении кол-ва зарегистрированных юзеров за неделю: " + e.stack);
        }
    }

    async getWeakRegistrationStat() {
        try {
            const stats = await userActivityService.getComprehensiveStatistics();
            return {
                weakRegistrationStat: stats.dailyRegistrations.map(d => ({
                    registeredUsers: d.count,
                    createdAt: d.dateStr
                })),
                weakRegisteredUserCount: stats.weekRegisteredUsersCount
            };
        } catch (e) {
            throw new Error("Ошибка при получении статистики регистраций юзеров: " + e.stack);
        }
    }
}

export default new UserRegistrationStatService();