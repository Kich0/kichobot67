import TeacherScheduleService from "../services/TeacherScheduleService.js";
import log from "../logging/logging.js";
import ApiError from "../exceptions/apiError.js";

class TeacherScheduleController {
    schedule_cache;

    constructor() {
        this.schedule_cache = {}
        // Очистка устаревших записей кэша каждые 5 минут
        setInterval(() => {
            const now = Date.now();
            let cleaned = 0;
            for (const key in this.schedule_cache) {
                if (now - this.schedule_cache[key].timestamp > 5 * 60 * 1000) {
                    delete this.schedule_cache[key];
                    cleaned++;
                }
            }
            if (cleaned > 0) {
                log.info(`[Teacher Cache Cleanup] Удалено ${cleaned} устаревших записей расписания преподавателей`);
            }
        }, 5 * 60 * 1000);
    }

    get_departments_list = async (req, res, next) => {
        try {
            const departments = await TeacherScheduleService.get_departments_list()
            return res.json(departments)
        } catch (e) {
            log.error("Ошибка при попытке получить список кафедр" + e.message, {stack: e.stack})
            next(e)
        }
    }

    get_teachers_list = async (req, res, next) => {
        try {
            const id = req.params.id
            if (isNaN(id)) {
                return next(ApiError.BadRequest("Указан некорректный параметр id кафедры"))
            }

            const teachers = await TeacherScheduleService.get_teachers_list(id)
            return res.json(teachers)
        } catch (e) {
            log.error("Ошибка при попытке получить список преподавателей кафедры" + e.message, {stack: e.stack})
            next(e)
        }
    }

    get_teacher_schedule = async (req, res, next) => {
        try {
            const id = req.params.id
            if (isNaN(id)) {
                return next(ApiError.BadRequest("Указан некорректный параметр id преподавателя"))
            }

            // Кэш 5 минут
            if (id in this.schedule_cache && Date.now() - this.schedule_cache[id].timestamp <= 5 * 60 * 1000) {
                return res.json(this.schedule_cache[id].schedule)
            }

            const schedule = await TeacherScheduleService.get_teacher_schedule(id)

            this.schedule_cache[id] = {schedule, timestamp: Date.now()}

            return res.json(schedule)
        } catch (e) {
            log.error("Ошибка при получении teacher расписания: " + e.message, {stack: e.stack})
            next(e)
        }
    }

}

export default new TeacherScheduleController()