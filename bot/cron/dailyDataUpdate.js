import {updateFacultiesCommandController} from "../controllers/commands/adminCommands/updateFaculties.js";
import {updateProgramsCommandController} from "../controllers/commands/adminCommands/updatePrograms.js";
import {updateGroupsCommandController} from "../controllers/commands/adminCommands/updateGroups.js";
import {updateProfilesCommandController} from "../controllers/commands/adminCommands/updateProfiles.js";
import {updateDepartmentsCommandController} from "../controllers/commands/adminCommands/updateDepartments.js";
import {updateTeachersCommandController} from "../controllers/commands/adminCommands/updateTeachers.js";
import log from "../logging/logging.js";
import cron from "node-cron";


export async function runDailyDataUpdate(isStartup = false) {
    const startTime = Date.now();
    log.info(`[Sync] Начинаю ${isStartup ? "стартовое" : "ежедневное"} обновление данных из КарГУ.`);

    try {
        log.info("[Sync] 1/6 Обновление факультетов...");
        await updateFacultiesCommandController(true);
    } catch (e) {
        log.error("[Sync] Ошибка при обновлении факультетов: " + e.message);
    }

    try {
        log.info("[Sync] 2/6 Обновление программ...");
        await updateProgramsCommandController(true);
    } catch (e) {
        log.error("[Sync] Ошибка при обновлении программ: " + e.message);
    }

    try {
        log.info("[Sync] 3/6 Обновление групп...");
        await updateGroupsCommandController(true);
    } catch (e) {
        log.error("[Sync] Ошибка при обновлении групп: " + e.message);
    }

    try {
        log.info("[Sync] 4/6 Обновление кафедр...");
        await updateDepartmentsCommandController(true);
    } catch (e) {
        log.error("[Sync] Ошибка при обновлении кафедр: " + e.message);
    }

    try {
        log.info("[Sync] 5/6 Обновление преподавателей...");
        await updateTeachersCommandController(true);
    } catch (e) {
        log.error("[Sync] Ошибка при обновлении преподавателей: " + e.message);
    }

    try {
        log.info("[Sync] 6/6 Обновление профилей...");
        await updateProfilesCommandController();
    } catch (e) {
        log.warn("[Sync] Пропуск обновления профилей: " + e.message);
    }

    const endTime = Date.now();
    log.info(`[Sync] Обновление данных КарГУ завершено. Время: ${Math.floor((endTime - startTime) / 1000)} сек.`);
}

export async function setupDailyDataUpdate(){
    // Запуск в 06:00 утра по времени Казахстана (Asia/Almaty, UTC+5)
    cron.schedule('00 6 * * *', async () => {
        await runDailyDataUpdate();
    }, {
        timezone: "Asia/Almaty"
    });
}