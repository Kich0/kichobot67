import {Schedule} from "../models/schedule.js"
import log from "../logging/logging.js";

class scheduleService {
    updateByGroupId = async (groupId, data) => {
        try {
            const numGroupId = Number(groupId) || groupId;
            const updated = await Schedule.findOneAndUpdate(
                { groupId: numGroupId }, { groupId: numGroupId, data }, { upsert: true, returnDocument: "after" }
            );

            return updated;
        } catch (e) {
            throw new Error("Ошибка при обновлении расписания по групАйди: " + e.stack)
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