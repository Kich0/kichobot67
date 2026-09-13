import {Router} from "express";
import log from "./logging/logging.js"
import groupService from "./services/groupService.js";
import userService from "./services/userService.js";
import scheduleService from "./services/scheduleService.js";
import LogService from "./services/logService.js";
import teacherService from "./services/teacherService.js";
import teacherScheduleService from "./services/teacherScheduleService.js";
import config from "./config.js";
import botHealthMonitor from "./utils/botHealthMonitor.js";
import {bot} from "./app.js";
// ПРЯМОЙ ИМПОРТ бэкенд-сервисов вместо HTTP
import BackendScheduleService from "../backend/services/ScheduleService.js";
import BackendTeacherScheduleService from "../backend/services/TeacherScheduleService.js";
import { getWebhookSecretToken } from "./utils/webhookRetry.js";
import authMiddleware from "../backend/middlewares/authMiddleware.js";

// Простой in-memory rate limiter для защиты публичных эндпоинтов от спама и DoS
function createRateLimiter(maxRequests, windowMs) {
    const clients = new Map();

    setInterval(() => {
        const now = Date.now();
        for (const [key, record] of clients.entries()) {
            if (now - record.startTime > windowMs) {
                clients.delete(key);
            }
        }
    }, 5 * 60 * 1000);

    return (req, res, next) => {
        const clientIp = req.ip || req.headers['x-forwarded-for'] || req.socket.remoteAddress || 'unknown';
        const now = Date.now();
        const record = clients.get(clientIp);

        if (!record || (now - record.startTime > windowMs)) {
            clients.set(clientIp, { count: 1, startTime: now });
            return next();
        }

        record.count++;
        if (record.count > maxRequests) {
            log.warn(`[Rate Limit] Превышен лимит запросов для IP: ${clientIp}`);
            return res.status(429).json({ error: "Слишком много запросов. Пожалуйста, подождите минуту." });
        }

        next();
    };
}

const scheduleRateLimiter = createRateLimiter(45, 60 * 1000); // 45 запросов в минуту
const logRateLimiter = createRateLimiter(10, 60 * 1000); // 10 запросов в минуту

const router = new Router()
router.get('/health', async (req, res) => {
    try {
        const healthStatus = await botHealthMonitor.getStatus();
        const statusCode = healthStatus.healthy ? 200 : 503;

        return res.status(statusCode).json({
            status: healthStatus.healthy ? 'healthy' : 'unhealthy',
            mode: config.BOT_MODE,
            ...healthStatus
        });
    } catch (e) {
        log.error('Error in health check endpoint', { stack: e.stack });
        return res.status(500).json({
            status: 'error',
            error: e.message
        });
    }
});
router.post('/webhook', async (req, res) => {
    try {
        // Проверка Secret Token Telegram для защиты от поддельных запросов
        const expectedToken = getWebhookSecretToken();
        if (config.BOT_MODE === 'webhook' && expectedToken) {
            const incomingToken = req.headers['x-telegram-bot-api-secret-token'];
            if (incomingToken !== expectedToken) {
                log.warn(`[Webhook Security] Отклонен неавторизованный запрос на вебхук от IP: ${req.ip}`);
                return res.sendStatus(403);
            }
        }

        const update = req.body;
        botHealthMonitor.updateActivity();
        await bot.processUpdate(update);

        return res.sendStatus(200);
    } catch (e) {
        log.error('Error processing webhook update', { stack: e.stack, update: req.body });
        return res.sendStatus(500);
    }
});
router.post('/webhook/test', authMiddleware, async (req, res) => {
    try {
        const testData = req.body;

        log.info('Test webhook call received', {
            mode: config.BOT_MODE,
            testData,
            timestamp: new Date().toISOString()
        });

        return res.status(200).json({
            success: true,
            mode: config.BOT_MODE,
            message: 'Test webhook endpoint is working',
            timestamp: new Date().toISOString(),
            receivedData: testData
        });
    } catch (e) {
        log.error('Error in test webhook endpoint', { stack: e.stack });
        return res.status(500).json({
            success: false,
            error: e.message
        });
    }
});

router.post("/log", logRateLimiter, async (req,res) => {
    const data = req.body;
    if (!data) return res.status(400).json({ error: "Empty body" });
    const str = typeof data === 'string' ? data : JSON.stringify(data);
    // Защита от DoS и переполнения диска логами (максимум 2KB)
    if (str.length > 2048) {
        return res.status(413).json({ error: "Payload too large (max 2KB)" });
    }
    log.warn(`[Client Log] ${str}`);
    return res.json('logged');
})

router.get('/get_user_schedule', scheduleRateLimiter, async (req, res) => {
    const rawUserId = req.query.userId;
    if (!rawUserId || isNaN(rawUserId)) {
        return res.status(400).json("Не указан или некорректен ID пользователя");
    }
    const userId = Number(rawUserId);
    if (!Number.isSafeInteger(userId) || userId <= 0) {
        return res.status(400).json("Некорректный userId");
    }

    log.info(`User ${userId} used a WebApp!`)
    const user = await userService.getUserById(userId)
    if (!user) {
        return res.json({
            studentSchedule:null,
            teacherSchedule:null
        })
    }

    const data = {scheduleType: user.scheduleType ?? 'student'}

    const group = await groupService.getById(user.group)
    if (group) {
        const groupId = group.id;
        const language = group.language;
        try {
            // Прямой вызов бэкенд-сервиса вместо HTTP
            const scheduleData = await BackendScheduleService.get_schedule_by_groupId(groupId, language);

            data.studentSchedule = {
                schedule:scheduleData,
                updatedAt: Date.now(),
                group,
                isNew:true
            }
        } catch (e) {
            const schedule = await scheduleService.getByGroupId(groupId)
            if (schedule) {
                const updateAt = new Date(schedule.updatedAt)
                data.studentSchedule = {
                    schedule: schedule.data,
                    updatedAt: updateAt.getTime(),
                    group,
                    isNew:false
                }
            }

        }
    }else{
        data.studentSchedule = null
    }

    const teacher = await teacherService.getById(user.teacher)
    if (teacher) {
        const teacherId = teacher.id;
        try {
            // Прямой вызов бэкенд-сервиса вместо HTTP
            const teacherScheduleData = await BackendTeacherScheduleService.get_teacher_schedule(teacherId);

            data.teacherSchedule = {
                schedule:teacherScheduleData,
                updatedAt: Date.now(),
                teacher,
                isNew:true
            }
        } catch (e) {
            const schedule = await teacherScheduleService.getByTeacherId(teacherId)
            if (schedule) {
                const updateAt = new Date(schedule.updatedAt)

                data.teacherSchedule = {
                    schedule:schedule.data,
                    updatedAt: updateAt,
                    teacher,
                    isNew:false
                }
            }

        }
    }else{
        data.teacherSchedule = null
    }

    return res.json(data)
})

router.get('/get_user_activity_logs', authMiddleware, async (req, res) => {
    const desiredLogLevels = req.query.levels ? req.query.levels.split(',') : [];
    const page = parseInt(req.query.page) || 1;
    const limit = parseInt(req.query.limit) || 50;
    const userId = parseInt(req.query.userId);
    const skip = (page - 1) * limit;

    const query = desiredLogLevels.length > 0 ? {level: {$in: desiredLogLevels}} : {};

    if (userId){
        query['meta.userId'] = userId
    }
    const totalDocuments = await LogService.getLogsCount(query)
    const documents = await LogService.getLogs(query, skip, limit)

    return res.json({
        page,
        limit,
        totalPages: Math.ceil(totalDocuments / limit),
        documents,
        desiredLogLevels
    });
})

export default router