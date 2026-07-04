import log from "../../../logging/logging.js";
import {sleep} from "../../../handlers/adminCommandHandler.js";
import departmentService from "../../../services/departmentService.js";
// ПРЯМОЙ ИМПОРТ бэкенд-сервиса вместо HTTP
import BackendTeacherScheduleService from "../../../../backend/services/TeacherScheduleService.js";

export async function updateDepartmentsCommandController(hard = false){
    async function getDepartmentList(attempts = 1) {
        try {
            // Прямой вызов вместо axios.get(KSU_HELPER_URL/...)
            return await BackendTeacherScheduleService.get_departments_list();
        } catch (e) {
            if (attempts >= 3) {
                log.error("[Sync Error] Не удалось получить список кафедр после 3 попыток. Прерываю.");
                throw e;
            }
            log.error(`Ошибка при получении списка кафедр (попытка ${attempts}/3). Жду 5 минут и пробую снова. Ошибка: ` + e.message, {stack: e.stack})
            await sleep(5 * 60 * 1000)
            return await getDepartmentList(attempts + 1)
        }
    }
    try{
        log.info("Начинаю обновление списка кафедр. hard = " + hard)

        const startTime = Date.now()

        const old_departments = await departmentService.getAll()
        const departments = await getDepartmentList()

        const endTime = Date.now()

        const availableRange = old_departments.length * 0.3;

        if (departments.length + availableRange >= old_departments.length || hard){
            await departmentService.updateAll(departments)
            log.info(`Обновление кафедр прошло успешно. Время выполнения:` +
                `${Math.floor((endTime - startTime) / 1000)} сек.\n` +
                `Было: ${old_departments.length} || Стало: ${departments.length} || Разница: ${departments.length - old_departments.length}`)
        }else{
            log.error("Полученных кафедр оказалось меньше чем было или равно. Я не стал их обновлять. " +
                "Время выполнения" + Math.floor((endTime - startTime) / 1000) + "сек." +
                `Было: ${old_departments.length}. Я получил: ${departments.length}`)
        }
        await sleep(1000)

    }catch (e) {
        log.error(`Произошла непредвиденная ошибка в updateProfilesCommandController() :` + e.message, {stack: e.stack})
    }
}