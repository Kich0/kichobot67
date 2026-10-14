import cron from "node-cron";
import log from "../logging/logging.js";
import { User } from "../models/user.js";
import { Group } from "../models/group.js";
import { Teacher } from "../models/teacher.js";
import { UserAction } from "../models/userAction.js";
import BackendScheduleService from "../../backend/services/ScheduleService.js";
import BackendTeacherScheduleService from "../../backend/services/TeacherScheduleService.js";
import buketovApiService from "../services/buketovApiService.js";
import scheduleService from "../services/scheduleService.js";
import teacherScheduleService from "../services/teacherScheduleService.js";
import { schedule_cache } from "../controllers/ScheduleController.js";
import { teacher_text_cache } from "../controllers/TeacherScheduleController.js";
import groupService from "../services/groupService.js";
import teacherService from "../services/teacherService.js";
import facultyService from "../services/facultyService.js";
import teacherDirectoryService from "../services/teacherDirectoryService.js";
import { enrichTeacherSchedule } from "../services/teacherScheduleEnricher.js";

export const KZ_OFFSET_MS = 5 * 60 * 60 * 1000;

/**
 * Проверка активности конвейера по времени Казахстана (Asia/Almaty, UTC+5).
 * Время работы: с 06:00 до 01:59 KZ.
 * Ночной сон: с 02:00 до 05:59 KZ (полное отключение фоновых сетевых запросов).
 */
export function isWarmupActiveKZ(date = new Date()) {
    const kzHour = new Date(date.getTime() + KZ_OFFSET_MS).getUTCHours();
    return kzHour < 2 || kzHour >= 6;
}

// Обратная совместимость с legacy
export function isDaytimeKZ(date = new Date()) {
    return isWarmupActiveKZ(date);
}

export class SmartWarmupEngine {
    constructor(options = {}) {
        // Конфигурация конвейера:
        // Группы: 150 групп * 4000 мс = 600 с (10 минут на полный круг)
        this.GROUP_INTERVAL_MS = options.groupIntervalMs ?? 4000;
        // Преподаватели: 30 человек * 10000 мс = 300 с (5 минут на полный круг)
        this.TEACHER_INTERVAL_MS = options.teacherIntervalMs ?? 10000;

        this.TOP_GROUPS_LIMIT = options.topGroupsLimit ?? 150;
        this.TOP_TEACHERS_LIMIT = options.topTeachersLimit ?? 30;
        this.TOP_LISTS_CACHE_TTL = options.topListsCacheTtl ?? 2 * 60 * 60 * 1000; // 2 часа

        this.topGroups = [];
        this.topTeachers = [];
        this.lastTopGroupsFetchAt = 0;
        this.lastTopTeachersFetchAt = 0;

        this.groupCursor = 0;
        this.teacherCursor = 0;

        this.groupTimer = null;
        this.teacherTimer = null;
        this.isGroupTicking = false;
        this.isTeacherTicking = false;
        this.isRunning = false;
    }

    /**
     * Динамический выбор топ-150 самых активных групп студентов
     */
    async refreshTopGroups() {
        try {
            const topAgg = await User.aggregate([
                { $match: { group: { $exists: true, $ne: null } } },
                { $group: { _id: "$group", count: { $sum: 1 } } },
                { $sort: { count: -1 } },
                { $limit: this.TOP_GROUPS_LIMIT }
            ]).catch(() => []);

            let groupIds = (topAgg || []).map(g => g._id).filter(Boolean);

            if (groupIds.length < this.TOP_GROUPS_LIMIT) {
                const extra = await Group.find({ id: { $nin: groupIds } })
                    .limit(this.TOP_GROUPS_LIMIT - groupIds.length)
                    .lean()
                    .catch(() => []);
                groupIds.push(...extra.map(g => g.id));
            }

            if (groupIds.length > 0) {
                const groups = await Group.find({ id: { $in: groupIds } }).lean().catch(() => []);
                const groupMap = new Map(groups.map(g => [g.id, g]));
                this.topGroups = groupIds.map(id => groupMap.get(id)).filter(Boolean);
            }

            this.lastTopGroupsFetchAt = Date.now();
            log.info(`[SmartWarmup] 🔄 Загружен топ-${this.topGroups.length} групп студентов для конвейера`);
        } catch (e) {
            log.warn(`[SmartWarmup] Ошибка обновления топ-групп: ${e.message}`);
        }
    }

    /**
     * Динамический выбор топ-30 преподавателей по User.teacher + UserAction
     */
    async refreshTopTeachers() {
        try {
            const userTeacherAgg = await User.aggregate([
                { $match: { teacher: { $exists: true, $ne: null } } },
                { $group: { _id: "$teacher", count: { $sum: 1 } } }
            ]).catch(() => []);

            let actionTeacherAgg = [];
            try {
                actionTeacherAgg = await UserAction.aggregate([
                    { 
                        $match: { 
                            action: { $regex: /teacher/i },
                            entityId: { $exists: true, $ne: null }
                        } 
                    },
                    { $group: { _id: "$entityId", count: { $sum: 1 } } },
                    { $sort: { count: -1 } },
                    { $limit: 50 }
                ]).catch(() => []);
            } catch (_) {}

            const scores = new Map();
            for (const item of (userTeacherAgg || [])) {
                if (item._id) {
                    const id = Number(item._id) || item._id;
                    scores.set(id, (scores.get(id) || 0) + item.count * 3);
                }
            }
            for (const item of (actionTeacherAgg || [])) {
                if (item._id) {
                    const id = Number(item._id) || item._id;
                    scores.set(id, (scores.get(id) || 0) + item.count);
                }
            }

            let sortedIds = Array.from(scores.entries())
                .sort((a, b) => b[1] - a[1])
                .map(([id]) => id);

            if (sortedIds.length < this.TOP_TEACHERS_LIMIT) {
                const extra = await Teacher.find({ id: { $nin: sortedIds } })
                    .limit(this.TOP_TEACHERS_LIMIT - sortedIds.length)
                    .lean()
                    .catch(() => []);
                sortedIds.push(...extra.map(t => t.id));
            } else {
                sortedIds = sortedIds.slice(0, this.TOP_TEACHERS_LIMIT);
            }

            if (sortedIds.length > 0) {
                const teachers = await Teacher.find({ id: { $in: sortedIds } }).lean().catch(() => []);
                const teacherMap = new Map(teachers.map(t => [t.id, t]));
                this.topTeachers = sortedIds.map(id => teacherMap.get(id)).filter(Boolean);
            }

            this.lastTopTeachersFetchAt = Date.now();
            log.info(`[SmartWarmup] 🔄 Загружен топ-${this.topTeachers.length} преподавателей для конвейера`);
        } catch (e) {
            log.warn(`[SmartWarmup] Ошибка обновления топ-преподавателей: ${e.message}`);
        }
    }

    /**
     * Одиночный тик конвейера студентов (1 группа каждые 4 сек)
     */
    async tickGroup() {
        if (this.isGroupTicking) return;
        this.isGroupTicking = true;
        try {
            if (!isWarmupActiveKZ()) {
                return; // Ночной сон (02:00-05:59 KZ)
            }

            if (!buketovApiService.canMakeApiCall(5)) {
                return; // Лимит 110 req/min близок или сайт КарУ лежит (API_DOWN)
            }

            if (!this.topGroups.length || (Date.now() - this.lastTopGroupsFetchAt > this.TOP_LISTS_CACHE_TTL)) {
                await this.refreshTopGroups();
            }

            if (!this.topGroups.length) return;

            const group = this.topGroups[this.groupCursor % this.topGroups.length];
            this.groupCursor = (this.groupCursor + 1) % this.topGroups.length;

            await this.warmupSingleGroup(group);
        } catch (err) {
            log.warn(`[SmartWarmup] Ошибка в tickGroup: ${err.message}`);
        } finally {
            this.isGroupTicking = false;
        }
    }

    /**
     * Прогрев одной группы студентов с сохранением в RAM и MongoDB
     */
    async warmupSingleGroup(group) {
        if (!group || !group.id) return;
        try {
            const lang = group.language || 'рус';
            const scheduleData = await BackendScheduleService.get_schedule_by_groupId(group.id, lang);
            if (!scheduleData) return;

            const groupIdent = `${group.id}|${lang}`;
            const botGroup = await groupService.getById(group.id).catch(() => group);
            let facultyId = 0;
            if (botGroup) {
                facultyId = await facultyService.getIdByGroup(botGroup).catch(() => 0) || 0;
            }

            const isFromDb = Boolean(scheduleData._fromDb);

            // 1. Сохраняем в RAM schedule_cache
            schedule_cache[groupIdent] = {
                data: scheduleData,
                timestamp: Date.now(),
                group: botGroup || group,
                facultyId,
                isFromDb
            };

            // 2. Сохраняем в MongoDB резерв (fire-and-forget)
            scheduleService.updateByGroupId(group.id, scheduleData).catch(e => {
                log.warn(`[SmartWarmup] Ошибка сохранения резерва MongoDB для группы ${group.id}: ${e.message}`);
            });
        } catch (e) {
            if (e.code !== 'NO_SCHEDULE') {
                log.warn(`[SmartWarmup] Ошибка прогрева группы ${group.id}: ${e.message}`);
            }
        }
    }

    /**
     * Одиночный тик конвейера преподавателей (1 преподаватель каждые 10 сек)
     */
    async tickTeacher() {
        if (this.isTeacherTicking) return;
        this.isTeacherTicking = true;
        try {
            if (!isWarmupActiveKZ()) {
                return; // Ночной сон (02:00-05:59 KZ)
            }

            if (!buketovApiService.canMakeApiCall(5)) {
                return; // Лимит 110 req/min близок или API_DOWN
            }

            if (!this.topTeachers.length || (Date.now() - this.lastTopTeachersFetchAt > this.TOP_LISTS_CACHE_TTL)) {
                await this.refreshTopTeachers();
            }

            if (!this.topTeachers.length) return;

            const teacher = this.topTeachers[this.teacherCursor % this.topTeachers.length];
            this.teacherCursor = (this.teacherCursor + 1) % this.topTeachers.length;

            await this.warmupSingleTeacher(teacher);
        } catch (err) {
            log.warn(`[SmartWarmup] Ошибка в tickTeacher: ${err.message}`);
        } finally {
            this.isTeacherTicking = false;
        }
    }

    /**
     * Прогрев одного преподавателя с сохранением в RAM и MongoDB
     */
    async warmupSingleTeacher(teacher) {
        if (!teacher || !teacher.id) return;
        try {
            const rawTeacher = await teacherService.getById(teacher.id).catch(() => teacher);
            const botTeacher = teacherDirectoryService.enrich(rawTeacher || teacher);

            const scheduleData = await BackendTeacherScheduleService.get_teacher_schedule(teacher.id, 'рус');
            if (!scheduleData) return;

            const isFromDb = Boolean(scheduleData._fromDb);
            const enrichedData = await enrichTeacherSchedule(scheduleData, botTeacher).catch(() => scheduleData);
            if (enrichedData) {
                enrichedData._fromDb = isFromDb;
            }

            // 1. Сохраняем в RAM teacher_text_cache
            teacher_text_cache[teacher.id] = {
                data: enrichedData || scheduleData,
                timestamp: Date.now(),
                teacher: botTeacher,
                departmentId: botTeacher?.department || teacher?.department || 0,
                _enriched: true,
                isFromDb
            };

            // 2. Сохраняем в MongoDB резерв (fire-and-forget)
            teacherScheduleService.updateByTeacherId(teacher.id, enrichedData || scheduleData).catch(e => {
                log.warn(`[SmartWarmup] Ошибка сохранения резерва MongoDB для преподавателя ${teacher.id}: ${e.message}`);
            });
        } catch (e) {
            log.warn(`[SmartWarmup] Ошибка прогрева преподавателя ${teacher.id}: ${e.message}`);
        }
    }

    start() {
        if (this.isRunning) return;
        this.isRunning = true;

        this.groupTimer = setInterval(() => {
            this.tickGroup().catch(e => log.warn(`[SmartWarmup] Необработанная ошибка тика группы: ${e.message}`));
        }, this.GROUP_INTERVAL_MS);
        if (this.groupTimer.unref) this.groupTimer.unref();

        this.teacherTimer = setInterval(() => {
            this.tickTeacher().catch(e => log.warn(`[SmartWarmup] Необработанная ошибка тика преподавателя: ${e.message}`));
        }, this.TEACHER_INTERVAL_MS);
        if (this.teacherTimer.unref) this.teacherTimer.unref();

        log.info(`[SmartWarmup] 🚀 Интеллектуальный конвейер прогрева запущен (группы: ${this.GROUP_INTERVAL_MS}мс, преподаватели: ${this.TEACHER_INTERVAL_MS}мс, активно 06:00-01:59 KZ)`);
    }

    stop() {
        if (this.groupTimer) {
            clearInterval(this.groupTimer);
            this.groupTimer = null;
        }
        if (this.teacherTimer) {
            clearInterval(this.teacherTimer);
            this.teacherTimer = null;
        }
        this.isRunning = false;
        log.info(`[SmartWarmup] 🛑 Конвейер прогрева остановлен`);
    }
}

export const smartWarmupEngine = new SmartWarmupEngine();

export function setupScheduleCacheWarmup() {
    smartWarmupEngine.start();

    // Гарантированное утреннее пробуждение конвейера в 06:00 KZ
    cron.schedule('0 6 * * *', () => {
        log.info("[SmartWarmup] 🌅 Утреннее пробуждение конвейера прогрева (06:00 KZ). Обновление топов...");
        smartWarmupEngine.refreshTopGroups().catch(() => {});
        smartWarmupEngine.refreshTopTeachers().catch(() => {});
    }, {
        timezone: "Asia/Almaty"
    });

    // Отложенная первичная загрузка топов через 15 секунд после старта сервера
    setTimeout(() => {
        if (isWarmupActiveKZ()) {
            smartWarmupEngine.refreshTopGroups().catch(() => {});
            smartWarmupEngine.refreshTopTeachers().catch(() => {});
        }
    }, 15 * 1000).unref();
}

export async function warmupTopSchedules() {
    // Legacy совместимость
    if (isWarmupActiveKZ()) {
        await smartWarmupEngine.refreshTopGroups();
        await smartWarmupEngine.refreshTopTeachers();
    }
}
