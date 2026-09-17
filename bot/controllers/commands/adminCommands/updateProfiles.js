import teacherProfileService from "../../../services/profileService.js";
import log from "../../../logging/logging.js";
import {sleep} from "../../../handlers/adminCommandHandler.js";
// ПРЯМОЙ ИМПОРТ бэкенд-контроллера вместо HTTP
import TeacherController from "../../../../backend/controllers/TeacherController.js";

export async function updateProfilesCommandController(hard = false){
    async function getProfileList(attempts = 1) {
        try {
            // Прямой вызов — TeacherController.get_all_teachers использует Puppeteer.
            // Раньше это шло через HTTP, сейчас вызываем метод напрямую.
            // Но get_all_teachers принимает (req, res, next) — это Express handler.
            // Нужно имитировать res.json для получения данных:
            return await new Promise((resolve, reject) => {
                const fakeReq = {};
                const fakeRes = { json: (data) => resolve(data) };
                const fakeNext = (err) => reject(err);
                TeacherController.get_all_teachers(fakeReq, fakeRes, fakeNext);
            });
        } catch (e) {
            if (attempts >= 3) {
                log.error("[Sync Error] Не удалось получить список профилей после 3 попыток. Прерываю.");
                throw e;
            }
            log.error(`Ошибка при получении списка профилей (попытка ${attempts}/3). Жду 5 минут и пробую снова. Ошибка: ` + e.message, {stack: e.stack})
            await sleep(5 * 60 * 1000)
            return await getProfileList(attempts + 1)
        }
    }

    try{
        log.info("[Profiles] Скрейпер профилей преподавателей через браузер перенесен в архив (Puppeteer отключен).");
        return;

        const endTime = Date.now()

        const availableRange = old_profiles.length * 0.3;

        if (profiles.length + availableRange >= old_profiles.length || hard){
            await teacherProfileService.updateAll(profiles)
            log.info(`Обновление профилей прошло успешно. Время выполнения:` +
                `${Math.floor((endTime - startTime) / 1000)} сек.\n` +
                `Было: ${old_profiles.length} || Стало: ${profiles.length} || Разница: ${profiles.length - old_profiles.length}`)
        }else{
            log.error("Полученных профилей оказалось меньше чем было или равно. Я не стал их обновлять. " +
                "Время выполнения" + Math.floor((endTime - startTime) / 1000) + "сек." +
                `Было: ${old_profiles.length}. Я получил: ${profiles.length}`)
        }
        await sleep(1000)

    }catch (e) {
        log.error(`Произошла непредвиденная ошибка в updateProfilesCommandController() :` + e.message, {stack: e.stack})
    }
}