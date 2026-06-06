import scheduleService from "../../../services/scheduleService.js";
import groupService from "../../../services/groupService.js";
import log from "../../../logging/logging.js";
import axios from "axios";
import {sleep} from "../../../handlers/adminCommandHandler.js";
import config from "../../../config.js";

export async function updateSchedulesCommandController(hard = false) {
    async function getSchedule(groupId, language) {
        try {
            const response = await axios.get(`${config.KSU_HELPER_URL}/express/api/schedule/get_schedule_by_groupId/${groupId}/${language}`)
            if (response.status === 200) {
                return response.data
            }
        } catch (e) {
            log.error(`Ошибка при получении расписания для группы ${groupId}. Ошибка: ` + e.message)
            await sleep(5000)
            return null
        }
    }

    try {
        log.info("Начинаю обновление расписаний всех групп. hard = " + hard)
        const startTime = Date.now()

        const groups = await groupService.getAll()
        let updatedCount = 0
        let errorCount = 0

        for (let i = 0; i < groups.length; i++) {
            const group = groups[i]
            await sleep(1000)

            const scheduleData = await getSchedule(group.id, group.language)
            
            if (scheduleData) {
                await scheduleService.updateByGroupId(group.id, scheduleData)
                updatedCount++
            } else {
                errorCount++
            }

            if (i % 10 === 0 || i === groups.length - 1) {
                const progress = Math.floor((i + 1) / groups.length * 100)
                log.info(`Обновление расписаний: ${progress}% (${i + 1}/${groups.length})`)
            }
        }

        const endTime = Date.now()
        log.info(`Обновление расписаний завершено. 
            Успешно: ${updatedCount}
            Ошибок: ${errorCount}
            Время выполнения: ${Math.floor((endTime - startTime) / 1000)} сек.`)

    } catch (e) {
        log.error(`Произошла непредвиденная ошибка в updateSchedulesCommandController() :` + e.message, {stack: e.stack})
    }
}
