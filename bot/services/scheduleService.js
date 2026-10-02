import {Schedule} from "../models/schedule.js"
import {ScheduleSnapshot} from "../models/scheduleSnapshot.js"
import log from "../logging/logging.js";

class scheduleService {
    updateByGroupId = async (groupId, data) => {
        try {
            const numGroupId = Number(groupId) || groupId;
            const updated = await Schedule.findOneAndUpdate(
                { groupId: numGroupId }, { groupId: numGroupId, data }, { upsert: true, returnDocument: "after" }
            );

            // Асинхронно сохраняем исторический снимок расписания с TTL 5 дней (не блокируя вызывающий поток)
            if (Array.isArray(data) && data.length > 0) {
                ScheduleSnapshot.create({ groupId: numGroupId, data }).catch(err => {
                    log.warn(`[ScheduleService] Не удалось сохранить снимок расписания для группы ${numGroupId}: ${err.message}`);
                });
            }

            return updated;
        } catch (e) {
            throw new Error("Ошибка при обновлении расписания по групАйди: " + e.stack)
        }
    }

    saveSnapshot = async (groupId, data) => {
        try {
            const numGroupId = Number(groupId) || groupId;
            return await ScheduleSnapshot.create({ groupId: numGroupId, data });
        } catch (e) {
            log.warn(`[ScheduleService] Ошибка сохранения снимка: ${e.message}`);
            return null;
        }
    }

    getByGroupId = async (groupId) => {
        try {
            const numGroupId = Number(groupId) || groupId;
            return await Schedule.findOne({ groupId: numGroupId });
        } catch (e) {
            throw new Error("Ошибка при получении расписания по группАйди: " + e.stack)
        }
    }
}


export default new scheduleService()