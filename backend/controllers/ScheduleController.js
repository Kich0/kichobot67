import log from "../logging/logging.js";
import ApiError from "../exceptions/apiError.js";
import ScheduleService from "../services/ScheduleService.js";
import BrowserController from "./BrowserController.js";

async function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

class ScheduleController {
    schedule_cache;

    constructor() {
        this.schedule_cache = {}
    }

    get_faculty_list = async (req, res, next) => {
        try {
            return res.json(BrowserController.faculties_data)
        } catch (e) {
            log.error("Ошибка при получении списка факультетов: ", e)
            next(e)
        }
    }

    get_program_list_by_facultyId = async (req, res, next) => {
        try {
            const id = req.params.id
            if (isNaN(id)) {
                return next(ApiError.BadRequest("Указан некорректный параметр id"))
            }

            const programs = await ScheduleService.get_program_list_by_facultyId(BrowserController.browser, BrowserController.faculties_data, id)
            return res.json(programs)


        } catch (e) {
            log.error("Ошибка при получении списка образовательных программ: ", e)
            next(e)
        }

    }

    get_group_list_by_programId = async (req, res, next) => {
        try {
            const id = req.params.id
            if (isNaN(id)) {
                return next(ApiError.BadRequest("Указан некорректный параметр id"))
            }
            const groups = await ScheduleService.get_group_list_by_programId(BrowserController.browser, id)
            return res.json(groups)
        } catch (e) {
            log.error("Ошибка при получении списка групп: ", e)
            next(e)
        }
    }

    get_schedule_by_groupId = async (req, res, next) => {
        try {
            const id = req.params.id
            const language = req.params.language
            if (isNaN(id) || !language) {
                return next(ApiError.BadRequest("Указан некорректный параметр id или language"))
            }
            const cacheName = id + language

            if (cacheName in this.schedule_cache && Date.now() - this.schedule_cache[cacheName].timestamp <= 15 * 1000) {
                return res.json(this.schedule_cache[cacheName].schedule)
            }

            const schedule = await ScheduleService.get_schedule_by_groupId(id, language)

            this.schedule_cache[cacheName] = {schedule, timestamp: Date.now()}

            return res.json(schedule)
        } catch (e) {
            log.error("Ошибка при получении student расписания: " + e.message + "\n\n На всякий случай запустил функцию authIfNot!", {stack: e.stack})
            next(e.message.includes("Navigation timeout of 3000 ms exceeded") || e.message.includes("net::ERR")? ApiError.ServiceUnavailable("Ксу не отвечает", [e.stack]) : e)
            await BrowserController.authIfNot()
        }
    }

    get_all_data = async (req, res, next) => {
        try {
            let all_programs_data = []
            let all_groups_data = []
            for (const faculty of BrowserController.faculties_data) {
                await delay(1000)
                const programs = await ScheduleService.get_program_list_by_facultyId(BrowserController.browser, BrowserController.faculties_data, faculty.id)
                for (const program of programs) {
                    await delay(2000)
                    all_programs_data.push(program)
                    const groups = await ScheduleService.get_group_list_by_programId(BrowserController.browser, program.id)
                    for (const group of groups) {
                        all_groups_data.push(group)
                    }
                }
            }
            return res.json({
                faculties: BrowserController.faculties_data,
                programs: all_programs_data,
                groups: all_groups_data
            })

        } catch (e) {
            log.error("Ошибка при получении списка всех данных: ", e)
            next(e)
        }

    }

}

export default new ScheduleController()