import cron from "node-cron";
import log from "../logging/logging.js";
import { User } from "../models/user.js";
import { Group } from "../models/group.js";
import BackendScheduleService from "../../backend/services/ScheduleService.js";
import { schedule_cache } from "../controllers/ScheduleController.js";
import groupService from "../services/groupService.js";

const KZ_OFFSET_MS = 5 * 60 * 60 * 1000;

function isDaytimeKZ() {
    const kzHour = new Date(Date.now() + KZ_OFFSET_MS).getUTCHours();
    return kzHour >= 7 && kzHour < 20;
}

async function warmupTopGroups() {
    try {
        if (!isDaytimeKZ()) {
            log.info("[CacheWarmup] Ночное время (вне 07:00-20:00 KZ). Прогрев пропущен для экономии ресурсов.");
            return;
        }

        log.info("[CacheWarmup] Начинаю умный прогрев кэша для топ-20 популярных групп...");
        
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
        
        // 2. Получаем данные о группах
        const groups = await Group.find({ id: { $in: topGroupIds } });
        
        let successCount = 0;
        for (const group of groups) {
            try {
                // Скачиваем расписание через бэкенд сервис напрямую
                const scheduleData = await BackendScheduleService.get_schedule_by_groupId(group.id, group.language);
                
                const groupIdent = `${group.id}|${group.language}`;
                const botGroup = await groupService.getById(group.id);
                const existing = schedule_cache[groupIdent];
                
                // Если в кэше уже есть свежие данные (меньше 30 минут), сохраняем timestamp,
                // чтобы не сбивать таймер "XX мин. назад" у активных пользователей
                const timestamp = (existing && (Date.now() - existing.timestamp < 30 * 60 * 1000))
                    ? existing.timestamp
                    : Date.now();

                schedule_cache[groupIdent] = { 
                    data: scheduleData, 
                    timestamp, 
                    group: botGroup 
                };
                
                successCount++;
                // Пауза между запросами для соблюдения лимитов API КарУ (120 req/min)
                await new Promise(r => setTimeout(r, 1500));
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
    // Запуск раз в час в дневное время (с 07:00 до 19:00 по времени Казахстана)
    cron.schedule('0 7-19 * * *', warmupTopGroups, {
        timezone: "Asia/Almaty"
    });
    
    // Фоновый запуск через 2 минуты после старта сервера (только если сейчас день)
    setTimeout(() => {
        if (isDaytimeKZ()) {
            warmupTopGroups();
        }
    }, 120 * 1000);
}
