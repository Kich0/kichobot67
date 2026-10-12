import {userRegistrationStat} from "../models/user-regiistration-stat.js";
import {User} from "../models/user.js";
import log from "../logging/logging.js";

class UserRegistrationStatService {
    getKZStartOfDay(daysAgo = 0) {
        const KZ_OFFSET_MS = 5 * 60 * 60 * 1000;
        const nowKz = new Date(Date.now() + KZ_OFFSET_MS);
        const startUtcMs = Date.UTC(nowKz.getUTCFullYear(), nowKz.getUTCMonth(), nowKz.getUTCDate() - daysAgo) - KZ_OFFSET_MS;
        return new Date(startUtcMs);
    }

    async getTodayRegisteredUserCount(){
        try{
            const today = this.getKZStartOfDay(0);
            return await User.countDocuments({createdAt: {$gte: today}});
        }catch (e) {
            throw new Error("Ошибка при получении кол-ва зарегестрированных юзеров сегодня: " + e.stack);
        }
    }

    async dailyRegisteredUserCountLogging(){
        try{
            const registeredUsers = await this.getTodayRegisteredUserCount();
            userRegistrationStat.create({registeredUsers})
                .then(() => log.warn("Произошла запись зарегестрированных юзеров!"));
        }catch (e) {
            throw new Error("Ошибка при логировании кол-ва зарегестрированных юзеров сегодня: " + e.stack);
        }
    }

    async getWeakRegisteredUserCount(){
        try{
            const oneWeekAgo = this.getKZStartOfDay(6);
            return await User.countDocuments({
                createdAt: { $gte: oneWeekAgo}
            });
        }catch (e) {
            throw new Error("Ошибка при получении кол-ва зарегестрированных юзеров неделю: " + e.stack);
        }
    }

    async getWeakRegistrationStat(){
        try{
            const weakRegistrationStat =  await userRegistrationStat.find().sort({createdAt:-1}).limit(7)
            const weakRegisteredUserCount = await this.getWeakRegisteredUserCount()
            return {weakRegistrationStat, weakRegisteredUserCount}
        }catch (e) {
            throw new Error("Ошибка при получении статистики регистраций юзеров в течении недели: " + e.stack)
        }
    }

}

export default new UserRegistrationStatService()