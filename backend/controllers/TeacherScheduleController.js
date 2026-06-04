import TeacherScheduleService from "../services/TeacherScheduleService.js";
import log from "../logging/logging.js";
import ApiError from "../exceptions/apiError.js";
import BrowserController from "./BrowserController.js";

class TeacherScheduleController {
    schedule_cache;

    constructor() {
        this.schedule_cache = {}
    }

    async get_departments_list(req, res, next) {
        try {
            const departments = await TeacherScheduleService.get_departments_list()
            return res.json(departments)
        } catch (e) {
            log.error("Ошибка при попытке получить список кафедр" + e.message, {stack: e.stack})
            next(e)
        }
    }

    async get_teachers_list(req, res, next) {
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

            if (id in this.schedule_cache && Date.now() - this.schedule_cache[id].timestamp <= 15 * 1000) {
                return res.json(this.schedule_cache[id].schedule)
            }

            const schedule = await TeacherScheduleService.get_teacher_schedule(id)

            this.schedule_cache[id] = {schedule, timestamp: Date.now()}

            return res.json(schedule)
        } catch (e) {
            log.error("Ошибка при получении teacher расписания: " + e.message + "\n\n На всякий случай запустил функцию authIfNot!", {stack: e.stack})
            next(e.message.includes("Navigation timeout of 3000 ms exceeded") || e.message.includes("ERR_ADDRESS_UNREACHABLE") ? ApiError.ServiceUnavailable("Ксу не отвечает", [e.stack]) : e)
            await BrowserController.authIfNot()
        }
    }

}

export default new TeacherScheduleController()