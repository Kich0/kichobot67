import log from "../logging/logging.js";
import https from "https";
import http from "http";
import BuketovApiService from "../../bot/services/buketovApiService.js";
import ScheduleApiAdapter from "../../bot/services/scheduleApiAdapter.js";
import { Group } from "../../bot/models/group.js";

export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// Глобальный Keep-Alive пул (экономия 200-350 мс на каждом TCP/TLS соединении к КарУ)
export const keepAliveHttpsAgent = new https.Agent({
    keepAlive: true,
    keepAliveMsecs: 60000,
    maxSockets: 50,
    maxFreeSockets: 20,
    timeout: 30000
});

export const keepAliveHttpAgent = new http.Agent({
    keepAlive: true,
    keepAliveMsecs: 60000,
    maxSockets: 50,
    maxFreeSockets: 20,
    timeout: 30000
});

class ScheduleService {
    /**
     * Получить расписание студента по groupId (официальный API + fallback).
     * ОСНОВНОЙ МЕТОД — вызывается из бота.
     */
    get_schedule_by_groupId = async (id, language) => {
        let currentGroup = null;

        // 1. Приоритетный путь: официальный API КарУ
        try {
            currentGroup = await Group.findOne({ id }).catch(() => null);
            if (currentGroup && currentGroup.name) {
                const apiData = await BuketovApiService.getGroupSchedule(currentGroup.name);
                if (apiData && Array.isArray(apiData.records)) {
                    if (apiData.records.length > 0) {
                        const schedule = ScheduleApiAdapter.adaptGroupSchedule(apiData.records, language);
                        schedule._fromDb = Boolean(apiData._fromDb);
                        return schedule;
                    } else if (!apiData._fromDb) {
                        // Официальный API успешно ответил (200 OK), но записей расписания для этой группы нет
                        const err = new Error(`У группы ${currentGroup.name} нет расписания`);
                        err.code = 'NO_SCHEDULE';
                        err.groupName = currentGroup.name;
                        throw err;
                    }
                }
            }
        } catch (apiErr) {
            if (apiErr.code === 'NO_SCHEDULE') {
                throw apiErr;
            }
            log.warn(`[Schedule] Ошибка BuketovApiService для группы id=${id}: ${apiErr.message}`);
        }

        // 2. Резервный Fallback: кэш из базы данных MongoDB
        try {
            const { Schedule } = await import("../../bot/models/schedule.js");
            const dbDoc = await Schedule.findOne({ groupId: id }).lean().catch(() => null);
            const scheduleData = dbDoc?.data || dbDoc?.schedule;
            if (Array.isArray(scheduleData) && scheduleData.length > 0) {
                const hasSubjects = scheduleData.some(day => Array.isArray(day.subjects) && day.subjects.some(s => s && s.subject && s.subject.trim() !== ''));
                if (hasSubjects) {
                    log.info(`[Schedule] Использован кэш MongoDB для группы id=${id}`);
                    scheduleData._fromDb = true;
                    if (dbDoc.updatedAt) {
                        scheduleData._updatedAt = dbDoc.updatedAt;
                    }
                    return scheduleData;
                }
            }
        } catch (dbErr) {
            log.warn(`[Schedule] Ошибка чтения кэша MongoDB: ${dbErr.message}`);
        }

        throw new Error(`Не удалось получить расписание для группы id=${id}`);
    };

    // ===========================
    // СТАБЫ ДЛЯ СОВМЕСТИМОСТИ (HTML-скрейпер удален)
    // ===========================

    get_faculty_list = async () => {
        log.warn("[Schedule] Legacy HTML scraper get_faculty_list отключен.");
        return [];
    };

    get_programs_by_faculty_name = async () => {
        log.warn("[Schedule] Legacy HTML scraper get_programs_by_faculty_name отключен.");
        return [];
    };

    get_program_list_by_facultyId = async () => {
        log.warn("[Schedule] Legacy HTML scraper get_program_list_by_facultyId отключен.");
        return [];
    };

    get_group_list_by_programId = async () => {
        log.warn("[Schedule] Legacy HTML scraper get_group_list_by_programId отключен.");
        return [];
    };

    get_all_groups_fast = async () => {
        log.warn("[Schedule] Legacy HTML scraper get_all_groups_fast отключен.");
        return [];
    };
}

export default new ScheduleService();