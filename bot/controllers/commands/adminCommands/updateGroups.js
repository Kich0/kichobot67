import groupService from "../../../services/groupService.js";
import programService from "../../../services/programService.js";
import log from "../../../logging/logging.js";
import BackendScheduleService from "../../../../backend/services/ScheduleService.js";
import { searchGroupMenuCache } from "../searchGroupCommandController.js";

export async function updateGroupsCommandController(hard = false) {
    try {
        log.info("Начинаю быстрое обновление списка групп (Axios+Cheerio). hard = " + hard);

        const startTime = Date.now();
        const old_groups = await groupService.getAll();
        const programs = await programService.getAll();

        if (!programs || programs.length === 0) {
            log.error("[UpdateGroups] В базе данных нет программ! Сначала обновите программы.");
            return;
        }

        log.info(`[UpdateGroups] Запуск параллельного сбора групп для ${programs.length} программ...`);
        const groups = await BackendScheduleService.get_all_groups_fast(programs, (stage, currentCount) => {
            if (stage % 25 === 0 || stage === 100) {
                log.info(`[UpdateGroups] Стадия: ${stage}%, получено групп: ${currentCount}`);
            }
        });

        const endTime = Date.now();
        const availableRange = old_groups.length * 0.3;

        if (groups && (groups.length + availableRange >= old_groups.length || hard || old_groups.length === 0)) {
            await groupService.updateAll(groups);

            // Очищаем кэш меню поиска, чтобы новые группы мгновенно находились
            try {
                if (searchGroupMenuCache) {
                    for (const key of Object.keys(searchGroupMenuCache)) {
                        delete searchGroupMenuCache[key];
                    }
                }
            } catch (ignore) {}

            log.info(`Обновление групп прошло успешно. Время выполнения: ` +
                `${Math.floor((endTime - startTime) / 1000)} сек.\n` +
                `Было: ${old_groups.length} || Стало: ${groups.length} || Разница: ${groups.length - old_groups.length}`);
            return groups;
        } else {
            log.error("Полученных групп оказалось меньше чем было или равно. Я не стал их обновлять. " +
                "Время выполнения: " + Math.floor((endTime - startTime) / 1000) + " сек. " +
                `Было: ${old_groups.length}. Я получил: ${groups?.length}`);
        }
    } catch (e) {
        log.error(`Произошла непредвиденная ошибка в updateGroupsCommandController(): ` + e.message, {stack: e.stack});
    }
}