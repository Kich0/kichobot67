import log from "../../../logging/logging.js";
import facultyService from "../../../services/facultyService.js";
import BackendScheduleService from "../../../../backend/services/ScheduleService.js";

export async function updateFacultiesCommandController(hard = false) {
    async function getFacultyList(attempts = 1) {
        try {
            return await BackendScheduleService.get_faculty_list();
        } catch (e) {
            if (attempts >= 3) {
                log.error("[Sync Error] Не удалось получить список факультетов после 3 попыток. Прерываю.");
                throw e;
            }
            log.error(`Ошибка при получении списка факультетов (попытка ${attempts}/3): ` + e.message, {stack: e.stack});
            await new Promise(r => setTimeout(r, 3000));
            return await getFacultyList(attempts + 1);
        }
    }

    try {
        log.info("Начинаю обновление списка факультетов. hard = " + hard);

        const startTime = Date.now();
        const old_faculties = await facultyService.getAll();
        const faculties = await getFacultyList();
        const endTime = Date.now();

        const availableRange = old_faculties.length * 0.3;

        if (faculties && (faculties.length + availableRange >= old_faculties.length || hard)) {
            await facultyService.updateAll(faculties);
            log.info(`Обновление факультетов прошло успешно. Время выполнения: ` +
                `${Math.floor((endTime - startTime) / 1000)} сек.\n` +
                `Было: ${old_faculties.length} || Стало: ${faculties.length} || Разница: ${faculties.length - old_faculties.length}`);
            return faculties;
        } else {
            log.error("Полученных факультетов оказалось меньше чем было или равно. Я не стал их обновлять. " +
                "Время выполнения " + Math.floor((endTime - startTime) / 1000) + "сек. " +
                `Было: ${old_faculties.length}. Я получил: ${faculties?.length}`);
        }
    } catch (e) {
        log.error(`Произошла непредвиденная ошибка в updateFacultiesCommandController() :` + e.message, {stack: e.stack});
    }
}