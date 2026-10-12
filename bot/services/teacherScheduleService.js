import {TeacherSchedule, TeacherScheduleVersion} from "../models/teacherSchedule.js"
import {saveVersioned, projectTeacherSchedule} from "./scheduleVersioning.js";

class teacherScheduleService {
    /**
     * Сохраняет расписание преподавателя с версионированием:
     * идентично -> только обновляется время проверки; изменилась хоть одна буква ->
     * старый оригинал уходит в историю (TeacherScheduleVersion), новый становится актуальным.
     */
    updateByTeacherId = async (teacherId, data) => {
        try {
            const id = Number(teacherId) || teacherId;
            return await saveVersioned({
                Model: TeacherSchedule,
                VersionModel: TeacherScheduleVersion,
                keyField: "teacherId",
                key: id,
                data,
                project: projectTeacherSchedule
            });
        } catch (e) {
            throw new Error("Ошибка при обновлении расписания по teacherId: " + e.stack)
        }
    }

    getByTeacherId = async (teacherId) => {
        try {
            const id = Number(teacherId) || teacherId;
            return await TeacherSchedule.findOne({ teacherId: id }).lean();
        } catch (e) {
            throw new Error("Ошибка при получении расписания по teacherId: " + e.stack)
        }
    }

    /** История прежних версий расписания преподавателя (от новых к старым). */
    getHistoryByTeacherId = async (teacherId, limit = 10) => {
        try {
            const id = Number(teacherId) || teacherId;
            return await TeacherScheduleVersion.find({ teacherId: id }).sort({ validTo: -1 }).limit(limit).lean();
        } catch (e) {
            throw new Error("Ошибка при получении истории расписания преподавателя: " + e.stack)
        }
    }
}


export default new teacherScheduleService()