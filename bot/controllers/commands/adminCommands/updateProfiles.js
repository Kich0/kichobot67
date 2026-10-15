import log from "../../../logging/logging.js";

export async function updateProfilesCommandController(hard = false) {
    try {
        log.info("[Profiles] Скрейпер профилей преподавателей через браузер перенесен в архив (Puppeteer отключен).");
        return;
    } catch (e) {
        log.error(`Произошла непредвиденная ошибка в updateProfilesCommandController() :` + e.message, { stack: e.stack });
    }
}