import log from "../logging/logging.js";
import BuketovApiService from "../../bot/services/buketovApiService.js";
import ScheduleApiAdapter from "../../bot/services/scheduleApiAdapter.js";
import { Teacher } from "../../bot/models/teacher.js";
import teacherService from "../../bot/services/teacherService.js";

class TeacherScheduleService {
    async get_departments_list() {
        log.warn("[TeacherSchedule] Legacy HTML scraper kafedra.php отключен (HTML scraper удален).");
        return [];
    }

    async get_teachers_list(departmentId) {
        log.warn(`[TeacherSchedule] Legacy HTML scraper report_prep.php для кафедры ${departmentId} отключен.`);
        return [];
    }

    async get_teacher_schedule(id, language = 'рус') {
        const numId = Number(id) || id;

        // 1. Приоритетный путь: официальный API КарУ
        try {
            const teacher = await teacherService.getById(numId).catch(() => null) || await Teacher.findOne({ id: numId }).catch(() => null);
            if (teacher && teacher.name) {
                const apiData = await BuketovApiService.getTeacherSchedule(teacher.name);
                if (apiData && Array.isArray(apiData.records)) {
                    if (apiData.records.length === 0 && apiData._fromDb) {
                        // Резерв MongoDB через BuketovApiService не нашел расписания — переходим к шагу 2
                    } else {
                        // Возвращаем расписание даже если records пуст (0 пар = 6 дней каникул/свободно)
                        const schedule = ScheduleApiAdapter.adaptTeacherSchedule(apiData.records, language);
                        schedule._fromDb = Boolean(apiData._fromDb);
                        return schedule;
                    }
                }
            }
        } catch (apiErr) {
            log.warn(`[TeacherSchedule] Ошибка BuketovApiService для преподавателя id=${id}: ${apiErr.message}`);
        }

        // 2. Резервный Fallback: кэш из базы данных MongoDB
        try {
            const { TeacherSchedule } = await import("../../bot/models/teacherSchedule.js");
            const dbDoc = await TeacherSchedule.findOne({ teacherId: numId }).lean().catch(() => null);
            if (dbDoc && Array.isArray(dbDoc.data) && dbDoc.data.length > 0) {
                log.info(`[TeacherSchedule] Использован кэш MongoDB для преподавателя id=${id}`);
                const schedule = dbDoc.data;
                schedule._fromDb = true;
                if (dbDoc.updatedAt) {
                    schedule._updatedAt = dbDoc.updatedAt;
                }
                return schedule;
            }
        } catch (dbErr) {
            log.warn(`[TeacherSchedule] Ошибка чтения кэша MongoDB: ${dbErr.message}`);
        }

        // 3. Финальный безопасный fallback: возвращаем корректную пустую сетку на 6 дней вместо краша бота
        log.warn(`[TeacherSchedule] Возврат пустой сетки для преподавателя id=${id}`);
        return ScheduleApiAdapter.adaptTeacherSchedule([], language);
    }
}

export default new TeacherScheduleService();