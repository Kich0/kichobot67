import {UserActivity} from "../models/user-activity.js";
import {User} from "../models/user.js";
import log from "../logging/logging.js";

class UserActivityService {
    getKZStartOfDay(daysAgo = 0) {
        const KZ_OFFSET_MS = 5 * 60 * 60 * 1000;
        const nowKz = new Date(Date.now() + KZ_OFFSET_MS);
        const startUtcMs = Date.UTC(nowKz.getUTCFullYear(), nowKz.getUTCMonth(), nowKz.getUTCDate() - daysAgo) - KZ_OFFSET_MS;
        return new Date(startUtcMs);
    }

    async getTodayUserCount() {
        try {
            const today = this.getKZStartOfDay(0);
            return await User.countDocuments({updatedAt: {$gte: today}});
        } catch (e) {
            throw new Error("Ошибка при получении кол-ва пользователей за сегодня: " + e.stack);
        }
    }

    async dailyUserActivityLogging() {
        try {
            const userActivity = await this.getTodayUserCount();
            UserActivity.create({userActivity})
                .then(() => log.warn("Произошла запись активности юзеров!"));
        } catch (e) {
            throw new Error("Ошибка при логировании дневной активности пользователей: " + e.stack);
        }
    }

    async getWeakUserActivity() {
        try {
            const weakUserActivity = await UserActivity.find().sort({createdAt: -1}).limit(7);
            const weakUserCount = await this.getWeakUserCount();
            return {weakUserActivity, weakUserCount};
        } catch (e) {
            throw new Error("Ошибка при получении активности пользователей за неделю: " + e.stack);
        }
    }

    async getWeakUserCount() {
        try {
            const oneWeekAgo = this.getKZStartOfDay(6);
            return await User.countDocuments({
                updatedAt: {$gte: oneWeekAgo}
            });
        } catch (e) {
            throw new Error("Ошибка при получении кол-ва пользователей за неделю: " + e.stack);
        }
    }

    async getUnactiveUsers() {
        try {
            const today = new Date();
            today.setHours(0, 0, 0, 0); // Обнуляем время для сравнения с началом дня

            const oneWeekAgo = new Date(today);
            oneWeekAgo.setDate(today.getDate() - 6);

            return await User.find({
                updatedAt: {$lte: oneWeekAgo} // Между одной неделей назад и сегодняшней датой
            });
        } catch (e) {
            throw new Error("Ошибка при получении неактивных пользователей: " + e.stack)
        }
    }
    async getAbsolutelyUnactiveUsers() {
        try {
            const today = new Date();
            today.setHours(0, 0, 0, 0); // Обнуляем время для сравнения с началом дня

            const oneWeekAgo = new Date(today);
            oneWeekAgo.setDate(today.getDate() - 21);

            return await User.find({
                updatedAt: {$lte: oneWeekAgo} // Между месяц назад и сегодняшней датой
            });
        } catch (e) {
            throw new Error("Ошибка при получении неактивных пользователей: " + e.stack)
        }
    }
}

export default new UserActivityService()