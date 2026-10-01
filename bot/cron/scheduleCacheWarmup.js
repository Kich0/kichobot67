import cron from "node-cron";
import log from "../logging/logging.js";
import { User } from "../models/user.js";
import { Group } from "../models/group.js";
import { Teacher } from "../models/teacher.js";
import BackendScheduleService from "../../backend/services/ScheduleService.js";
import BackendTeacherScheduleService from "../../backend/services/TeacherScheduleService.js";
import { schedule_cache } from "../controllers/ScheduleController.js";
import { teacher_text_cache } from "../controllers/TeacherScheduleController.js";
import groupService from "../services/groupService.js";
import teacherService from "../services/teacherService.js";

const KZ_OFFSET_MS = 5 * 60 * 60 * 1000;

function isDaytimeKZ() {
    const kzHour = new Date(Date.now() + KZ_OFFSET_MS).getUTCHours();
    return kzHour >= 7 && kzHour <= 22; // 07:00 - 22:59 KZ (охватывает пик 21:00-22:00)
}

async function warmupTopSchedules() {
    try {
        if (!isDaytimeKZ()) {
            log.info("[CacheWarmup] Ночное время (вне 07:00-22:30 KZ). Прогрев пропущен для экономии ресурсов.");
            return;
        }

        log.info("[CacheWarmup] Начинаю умный прогрев кэша (топ-20 групп + топ-10 преподавателей)...");
        
        // 1. Находим топ-20 самых популярных групп среди пользователей
        const topGroupsAggregation = await User.aggregate([
            { $match: { group: { $exists: true, $ne: null } } },
            { $group: { _id: "$group", count: { $sum: 1 } } },
            { $sort: { count: -1 } },
            { $limit: 20 }
        ]);

        let successGroupsCount = 0;
        if (topGroupsAggregation && topGroupsAggregation.length > 0) {
            const topGroupIds = topGroupsAggregation.map(g => g._id);
            const groups = await Group.find({ id: { $in: topGroupIds } });
            
            for (const group of groups) {
                try {
                    const scheduleData = await BackendScheduleService.get_schedule_by_groupId(group.id, group.language);
                    const groupIdent = `${group.id}|${group.language}`;
                    const botGroup = await groupService.getById(group.id);
                    const existing = schedule_cache[groupIdent];
                    
                    const timestamp = (existing && (Date.now() - existing.timestamp < 30 * 60 * 1000))
                        ? existing.timestamp
                        : Date.now();

                    schedule_cache[groupIdent] = { 
                        data: scheduleData, 
                        timestamp, 
                        group: botGroup 
                    };
                    
                    successGroupsCount++;
                    // Задержка между запросами для соблюдения лимитов API КарУ (120 req/min)
                    await new Promise(r => setTimeout(r, 1500));
                } catch (e) {
                    log.warn(`[CacheWarmup] Ошибка загрузки расписания для группы ${group.id}: ${e.message}`);
                }
            }
        }

        // 2. Находим топ-10 самых популярных преподавателей среди пользователей
        let successTeachersCount = 0;
        try {
            const topTeachersAggregation = await User.aggregate([
                { $match: { teacher: { $exists: true, $ne: null } } },
                { $group: { _id: "$teacher", count: { $sum: 1 } } },
                { $sort: { count: -1 } },
                { $limit: 10 }
            ]);

            if (topTeachersAggregation && topTeachersAggregation.length > 0) {
                const topTeacherIds = topTeachersAggregation.map(t => t._id);
                const teachers = await Teacher.find({ id: { $in: topTeacherIds } });

                for (const teacher of teachers) {
                    try {
                        const scheduleData = await BackendTeacherScheduleService.get_teacher_schedule(teacher.id);
                        const botTeacher = await teacherService.getById(teacher.id);
                        const existing = teacher_text_cache[teacher.id];

                        const timestamp = (existing && (Date.now() - existing.timestamp < 30 * 60 * 1000))
                            ? existing.timestamp
                            : Date.now();

                        teacher_text_cache[teacher.id] = {
                            data: scheduleData,
                            timestamp,
                            teacher: botTeacher,
                            departmentId: botTeacher?.department,
                            _enriched: true
                        };

                        successTeachersCount++;
                        await new Promise(r => setTimeout(r, 1500));
                    } catch (te) {
                        log.warn(`[CacheWarmup] Ошибка загрузки преподавателя ${teacher.id}: ${te.message}`);
                    }
                }
            }
        } catch (err) {
            log.warn(`[CacheWarmup] Пропуск прогрева преподавателей: ${err.message}`);
        }
        
        log.info(`[CacheWarmup] Прогрев завершён! Обновлено: ${successGroupsCount} групп, ${successTeachersCount} преподавателей.`);
    } catch (e) {
        log.error(`[CacheWarmup] Критическая ошибка: ${e.message}`, { stack: e.stack });
    }
}

export function setupScheduleCacheWarmup() {
    // Запуск раз в час в активное время (с 07:00 до 22:00 по времени Казахстана)
    cron.schedule('0 7-22 * * *', warmupTopSchedules, {
        timezone: "Asia/Almaty"
    });
    
    // Фоновый запуск через 2 минуты после старта сервера (только если сейчас дневное время)
    setTimeout(() => {
        if (isDaytimeKZ()) {
            warmupTopSchedules();
        }
    }, 120 * 1000);
}
