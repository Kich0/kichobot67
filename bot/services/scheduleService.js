import {Schedule, ScheduleVersion} from "../models/schedule.js"
import {saveVersioned, projectGroupSchedule} from "./scheduleVersioning.js";

class scheduleService {
    /**
     * Сохраняет расписание группы с версионированием:
     * идентично -> только обновляется время проверки; изменилась хоть одна буква ->
     * старый оригинал уходит в историю (ScheduleVersion), новый становится актуальным.
     */
    updateByGroupId = async (groupId, data) => {
        try {
            const numGroupId = Number(groupId) || groupId;
            return await saveVersioned({
                Model: Schedule,
                VersionModel: ScheduleVersion,
                keyField: "groupId",
                key: numGroupId,
                data,
                project: projectGroupSchedule
            });
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

    /** История прежних версий расписания группы (от новых к старым). */
    getHistoryByGroupId = async (groupId, limit = 10) => {
        try {
            const numGroupId = Number(groupId) || groupId;
            return await ScheduleVersion.find({ groupId: numGroupId }).sort({ validTo: -1 }).limit(limit).lean();
        } catch (e) {
            throw new Error("Ошибка при получении истории расписания группы: " + e.stack)
        }
    }
}


export default new scheduleService()