import ScheduleService from "./ScheduleService.js";
import { Faculty } from "../models/Faculty.js";
import { Program } from "../models/Program.js";
import { UniversityGroup } from "../models/UniversityGroup.js";
import { UniversitySchedule } from "../models/UniversitySchedule.js";
import log from "../logging/logging.js";
import BrowserController from "../controllers/BrowserController.js";

class SyncService {
    async syncAll() {
        try {
            log.info("[Sync] Начинаю полную синхронизацию данных с КСУ");
            const startTime = Date.now();

            if (!BrowserController.browser || !BrowserController.browser.isConnected()) {
                await BrowserController.launchBrowser();
            }

            if (!BrowserController.faculties_data) {
                await BrowserController.auth();
            }

            const faculties = BrowserController.faculties_data;
            if (!faculties) throw new Error("Не удалось получить список факультетов");

            // 1. Получаем все программы в память
            let allPrograms = [];
            for (const faculty of faculties) {
                log.info(`[Sync] Получаю программы для факультета: ${faculty.name}`);
                const programs = await ScheduleService.get_program_list_by_facultyId(BrowserController.browser, faculties, faculty.id);
                allPrograms.push(...programs.map(p => ({
                    name: p.name,
                    id: p.id,
                    faculty: faculty.id,
                    facultyName: p.facultyName
                })));
                await new Promise(r => setTimeout(r, 1000));
            }

            // 2. Получаем все группы в память
            let allGroups = [];
            for (const program of allPrograms) {
                log.info(`[Sync] Получаю группы для программы: ${program.name}`);
                try {
                    const groups = await ScheduleService.get_group_list_by_programId(BrowserController.browser, program.id);
                    allGroups.push(...groups.map(g => ({
                        name: g.name,
                        id: g.id,
                        language: g.language,
                        href: g.href,
                        age: g.age,
                        studentCount: g.studentCount,
                        program: program.id
                    })));
                } catch (e) {
                    log.error(`[Sync] Ошибка при получении групп для программы ${program.id}: ${e.message}`);
                }
                await new Promise(r => setTimeout(r, 1000));
            }

            // 3. Записываем скачанные метаданные в БД (только когда всё успешно получено)
            log.info("[Sync] Все метаданные успешно получены в память. Начинаю обновление БД...");
            
            await Faculty.deleteMany({});
            await Faculty.insertMany(faculties);
            log.info(`[Sync] Сохранено ${faculties.length} факультетов`);

            await Program.deleteMany({});
            await Program.insertMany(allPrograms);
            log.info(`[Sync] Сохранено ${allPrograms.length} программ`);

            await UniversityGroup.deleteMany({});
            await UniversityGroup.insertMany(allGroups);
            log.info(`[Sync] Сохранено ${allGroups.length} групп`);

            // 4. Получаем и сохраняем расписания
            // Это самая тяжелая часть, будем делать по одной группе с задержкой
            log.info(`[Sync] Начинаю скачивание расписаний для ${allGroups.length} групп...`);
            let scheduleCount = 0;
            for (const group of allGroups) {
                try {
                    const scheduleData = await ScheduleService.get_schedule_by_groupId(group.id, group.language);
                    await UniversitySchedule.findOneAndUpdate(
                        { groupId: group.id },
                        { groupId: group.id, data: scheduleData },
                        { upsert: true }
                    );
                    scheduleCount++;
                    if (scheduleCount % 10 === 0) {
                        log.info(`[Sync] Скачано расписаний: ${scheduleCount}/${allGroups.length}`);
                    }
                } catch (e) {
                    log.error(`[Sync] Ошибка при получении расписания для группы ${group.id}: ${e.message}`);
                }
                await new Promise(r => setTimeout(r, 1000));
            }

            const endTime = Date.now();
            log.info(`[Sync] Полная синхронизация завершена за ${Math.floor((endTime - startTime) / 1000)} сек. Успешно обновлено расписаний: ${scheduleCount}`);
        } catch (e) {
            log.error(`[Sync] Критическая ошибка при синхронизации: ${e.message}`, { stack: e.stack });
        }
    }
}

export default new SyncService();
