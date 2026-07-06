import cron from "node-cron";
import log from "../logging/logging.js";
import { User } from "../models/user.js";
import { UniversityGroup } from "../../backend/models/UniversityGroup.js";
import BackendScheduleService from "../../backend/services/ScheduleService.js";
import { schedule_cache } from "../controllers/ScheduleController.js";
import groupService from "../services/groupService.js";

async function warmupTopGroups() {
    try {
        log.info("[CacheWarmup] Начинаю прогрев кэша для популярных групп...");
        
        // 1. Находим топ-20 самых популярных групп среди пользователей
        const topGroupsAggregation = await User.aggregate([
            { $match: { group: { $exists: true, $ne: null } } },
            { $group: { _id: "$group", count: { $sum: 1 } } },
            { $sort: { count: -1 } },
            { $limit: 20 }
        ]);

        if (!topGroupsAggregation || topGroupsAggregation.length === 0) {
            log.info("[CacheWarmup] Пользователей с группами не найдено, прогрев отменён.");
            return;
        }

        const topGroupIds = topGroupsAggregation.map(g => g._id);
        
        // 2. Получаем данные о группах для извлечения языка (Otdel)
        const groups = await UniversityGroup.find({ id: { $in: topGroupIds } });
        
        let successCount = 0;
        for (const group of groups) {
            try {
                // Скачиваем расписание через бэкенд сервис напрямую
                const scheduleData = await BackendScheduleService.get_schedule_by_groupId(group.id, group.language);
                
                // Кэшируем в боте
                const groupIdent = `${group.id}|${group.language}`;
                const botGroup = await groupService.getById(group.id);
                
                schedule_cache[groupIdent] = { 
                    data: scheduleData, 
                    timestamp: Date.now(), 
                    group: botGroup 
                };
                
                successCount++;
                // Ждём чуть-чуть чтобы не DDoSit КарГУ
                await new Promise(r => setTimeout(r, 2000));
            } catch (e) {
                log.warn(`[CacheWarmup] Ошибка загрузки расписания для группы ${group.id}: ${e.message}`);
            }
        }
        
        log.info(`[CacheWarmup] Прогрев завершён! Обновлено ${successCount} групп.`);
    } catch (e) {
        log.error(`[CacheWarmup] Критическая ошибка: ${e.message}`, { stack: e.stack });
    }
}

export function setupScheduleCacheWarmup() {
    // Запускаем каждые 20 минут
    cron.schedule('*/20 * * * *', warmupTopGroups);
    
    // И через 1 минуту после старта сервера
    setTimeout(warmupTopGroups, 60 * 1000);
}
