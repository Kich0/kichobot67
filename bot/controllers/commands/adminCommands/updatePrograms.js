import log from "../../../logging/logging.js";
import programService from "../../../services/programService.js";
import facultyService from "../../../services/facultyService.js";
import BackendScheduleService from "../../../../backend/services/ScheduleService.js";

export async function updateProgramsCommandController(hard = false) {
    async function getProgramList(faculty, attempts = 1) {
        try {
            return await BackendScheduleService.get_programs_by_faculty_name(faculty.name, faculty.id);
        } catch (e) {
            if (attempts >= 3) {
                log.error(`[Sync Error] Не удалось получить список программ для факультета ${faculty.name} после 3 попыток.`);
                return [];
            }
            log.error(`Ошибка при получении списка программ (${faculty.name}, попытка ${attempts}/3): ` + e.message);
            await new Promise(r => setTimeout(r, 1000));
            return await getProgramList(faculty, attempts + 1);
        }
    }

    try {
        log.info("Начинаю обновление списка программ. hard = " + hard);

        const startTime = Date.now();
        const old_programs = await programService.getAll();
        let programs = [];

        const faculties = await facultyService.getAll();

        for (const faculty of faculties) {
            await new Promise(r => setTimeout(r, 200));

            const program_list = await getProgramList(faculty);

            for (const program of program_list) {
                programs.push({
                    name: program['name'],
                    id: program['id'],
                    href: program['href'],
                    faculty: program['facultyId'] !== undefined ? program['facultyId'] : faculty.id,
                });
            }

            const stage = Math.floor((faculties.indexOf(faculty) + 1) / faculties.length * 100);
            log.info(`Получены программы факультета: ${faculty.name} (${program_list.length} шт). ` +
                `Стадия: ${stage}%`);
        }

        const availableRange = old_programs.length * 0.3;
        const endTime = Date.now();

        if (programs.length + availableRange >= old_programs.length || hard) {
            await programService.updateAll(programs);
            log.info(`Обновление программ прошло успешно. Время выполнения: ` +
                `${Math.floor((endTime - startTime) / 1000)} сек.\n` +
                `Было: ${old_programs.length} || Стало: ${programs.length} || Разница: ${programs.length - old_programs.length}`);
            return programs;
        } else {
            log.error("Полученных программ оказалось меньше чем было или равно. Я не стал их обновлять. " +
                "Время выполнения " + Math.floor((endTime - startTime) / 1000) + "сек. " +
                `Было: ${old_programs.length}. Я получил: ${programs.length}`);
        }
    } catch (e) {
        log.error(`Произошла непредвиденная ошибка в updateProgramsCommandController() :` + e.message, {stack: e.stack});
    }
}