import config from "../../config.js";
import log from "../logging/logging.js";
import { cleanTitles, normalizeCyrillic } from "./teacherDirectoryService.js";

class BuketovApiService {
    constructor() {
        // Раздельные LRU-кэши в оперативной памяти:
        // 1. studentCache — 1500 слотов под группы студентов
        this.studentCache = new Map();
        this.STUDENT_CAPACITY = 1500;

        // 2. teacherCache — 500 слотов под преподавателей
        // Трафик студентов НИКОГДА не вытесняет расписание преподавателей!
        this.teacherCache = new Map();
        this.TEACHER_CAPACITY = 500;

        this.cacheTTL = 10 * 60 * 1000; // 10 минут локального кэша

        // Circuit Breaker (Автоматический предохранитель)
        this.circuitState = 'CLOSED'; // 'CLOSED' | 'API_DOWN' | 'HALF_OPEN'
        this.consecutiveFailures = 0;
        this.circuitTrippedAt = 0;
        this.CIRCUIT_COOLDOWN_MS = 2 * 60 * 1000; // 2 минуты в состоянии API_DOWN
        this.FAILURE_THRESHOLD = 2; // 2 подряд сбоя -> трип
        this.REQUEST_TIMEOUT_MS = 4500; // 4.5 секунды таймаут запроса (AbortController)

        // Outbound Rate Limiter (Железный предохранитель 110 req/min)
        this.MAX_REQUESTS_PER_MINUTE = 110;
        this.outboundRequestTimestamps = [];

        // Фоновая очистка протухших записей кэша раз в 5 минут
        setInterval(() => {
            const now = Date.now();
            for (const [k, v] of this.studentCache.entries()) {
                if (v && (now - v.timestamp > this.cacheTTL)) {
                    this.studentCache.delete(k);
                }
            }
            for (const [k, v] of this.teacherCache.entries()) {
                if (v && (now - v.timestamp > this.cacheTTL)) {
                    this.teacherCache.delete(k);
                }
            }
        }, 5 * 60 * 1000).unref();
    }

    // Совместимость с любым legacy кодом
    get cache() {
        return this.studentCache;
    }

    /**
     * Получение из LRU кэша с обновлением порядка использования
     */
    _getCache(cache, key) {
        if (!cache.has(key)) return null;
        const entry = cache.get(key);
        // Обновляем позицию для LRU (удаляем и вставляем в конец Map)
        cache.delete(key);
        cache.set(key, entry);
        return entry;
    }

    /**
     * Запись в LRU кэш с вытеснением самого старого при переполнении
     */
    _setCache(cache, capacity, key, value) {
        if (cache.has(key)) {
            cache.delete(key);
        } else if (cache.size >= capacity) {
            // Удаляем первый (наименее используемый) элемент Map
            const oldestKey = cache.keys().next().value;
            if (oldestKey !== undefined) {
                cache.delete(oldestKey);
            }
        }
        cache.set(key, value);
    }

    /**
     * Фиксация успешного ответа API для Circuit Breaker
     */
    _recordSuccess() {
        if (this.circuitState !== 'CLOSED') {
            log.info(`[BuketovApiService] ✅ Сервер КарУ восстановился! Circuit Breaker сброшен в CLOSED.`);
        }
        this.consecutiveFailures = 0;
        this.circuitState = 'CLOSED';
    }

    _recordFailure(err) {
        this.consecutiveFailures++;
        log.warn(`[BuketovApiService] Сбой API КарУ (${this.consecutiveFailures}/${this.FAILURE_THRESHOLD}): ${err?.message || err}`);
        if (this.circuitState === 'HALF_OPEN' || this.consecutiveFailures >= this.FAILURE_THRESHOLD) {
            this.circuitState = 'API_DOWN';
            this.circuitTrippedAt = Date.now();
            this.consecutiveFailures = this.FAILURE_THRESHOLD;
            log.error(`[BuketovApiService] 🚨 Circuit Breaker СРАБОТАЛ! Переход в состояние API_DOWN на 2 минуты. Запросы сразу перенаправляются в MongoDB fallback.`);
        }
    }

    /**
     * Проверка скользящего 60-секундного окна исходящих сетевых запросов.
     * Возвращает false, если достигнут защитный лимит (>= 110 req/min).
     * @param {number} headroom - Дополнительный запас слотов (например, для фонового конвейера)
     */
    checkRateLimit(headroom = 0) {
        const now = Date.now();
        const cutoff = now - 60000;
        this.outboundRequestTimestamps = this.outboundRequestTimestamps.filter(ts => ts > cutoff);
        return this.outboundRequestTimestamps.length < (this.MAX_REQUESTS_PER_MINUTE - headroom);
    }

    /**
     * Публичный метод проверки возможности сделать сетевой запрос к API КарУ прямо сейчас.
     */
    canMakeApiCall(headroom = 0) {
        return this.checkRateLimit(headroom) && this.circuitState !== 'API_DOWN';
    }

    /**
     * Очистка звания преподавателя для точного совпадения в API
     * Например: "ст.преп. Попова Н. В." -> "Попова Н. В."
     */
    cleanTeacherName(rawName) {
        if (!rawName) return '';
        return cleanTitles(rawName);
    }

    /**
     * Преобразование расписания группы из MongoDB в формат записей API
     */
    _dbScheduleToRecords(days = [], groupName = '') {
        const records = [];
        const dayMap = {
            'понедельник': 1, 'дүйсенбі': 1,
            'вторник': 2, 'сейсенбі': 2,
            'среда': 3, 'сәрсенбі': 3,
            'четверг': 4, 'бейсенбі': 4,
            'пятница': 5, 'жұма': 5,
            'суббота': 6, 'сенбі': 6,
            'воскресенье': 7, 'жексенбі': 7
        };

        for (const d of days) {
            const dayName = (d.day || '').trim();
            const dayIndex = dayMap[dayName.toLowerCase()] || 0;
            if (Array.isArray(d.subjects)) {
                for (let slotIdx = 0; slotIdx < d.subjects.length; slotIdx++) {
                    const item = d.subjects[slotIdx];
                    if (!item || !item.subject || !item.subject.trim()) continue;

                    // Поддержка нескольких подгрупп/блоков, разделенных символом книги 📚
                    const blocks = item.subject.split(/(?:\r?\n)?📚\s*/).filter(b => b && b.trim());
                    for (const block of blocks) {
                        const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
                        if (lines.length === 0) continue;

                        let rawDiscipline = lines[0] || '';
                        let teacher = lines[1] || '';
                        let roomInfo = lines[2] || '';

                        // Если преподаватель и аудитория оказались на 2-й строке
                        if (lines.length === 2 && !roomInfo) {
                            const match = lines[1].match(/(.+?)\s+(Ауд\.?\s*[^\s]+|\d+\/\d+)$/i);
                            if (match) {
                                teacher = match[1].trim();
                                roomInfo = match[2].trim();
                            }
                        }

                        // Выделяем тип занятия, если он есть в названии
                        let type = '';
                        const typeMatch = rawDiscipline.match(/\/(?:Лекция|Прак\.зан\.|Лаб\.раб\.|СРОП|[^\/]+)\//i);
                        if (typeMatch) {
                            type = typeMatch[0].replace(/\//g, '').trim();
                        }

                        // Парсим номер аудитории и корпус
                        let room = '';
                        let building = '';
                        if (roomInfo) {
                            const cleanRoom = roomInfo.replace(/^Ауд\.?\s*/i, '').trim();
                            if (cleanRoom.includes('/')) {
                                const parts = cleanRoom.split('/');
                                room = parts[0].trim();
                                building = parts[1].trim();
                            } else {
                                room = cleanRoom;
                            }
                        }

                        records.push({
                            day: dayName,
                            dayIndex,
                            slot: slotIdx + 1,
                            time: item.time || '',
                            subject: rawDiscipline,
                            discipline: rawDiscipline,
                            type,
                            lessonType: type,
                            teacher,
                            room,
                            building,
                            group: groupName
                        });
                    }
                }
            }
        }
        return records;
    }

    /**
     * Преобразование расписания преподавателя из MongoDB в формат записей API
     */
    _dbTeacherScheduleToRecords(days = [], teacherName = '') {
        const records = [];
        const dayMap = {
            'понедельник': 1, 'дүйсенбі': 1,
            'вторник': 2, 'сейсенбі': 2,
            'среда': 3, 'сәрсенбі': 3,
            'четверг': 4, 'бейсенбі': 4,
            'пятница': 5, 'жұма': 5,
            'суббота': 6, 'сенбі': 6,
            'воскресенье': 7, 'жексенбі': 7
        };

        for (const d of days) {
            const dayName = (d.day || '').trim();
            const dayIndex = dayMap[dayName.toLowerCase()] || 0;
            if (Array.isArray(d.groups)) {
                for (let slotIdx = 0; slotIdx < d.groups.length; slotIdx++) {
                    const item = d.groups[slotIdx];
                    if (!item || (!item.group && !item.subject)) continue;

                    let room = item.room || '';
                    let building = item.building || '';
                    if (!room && item.group) {
                        const roomMatch = item.group.match(/\((?:Ауд\.\s*)?([^/)]+)(?:\/([^)]+))?\)/i);
                        if (roomMatch) {
                            room = roomMatch[1].trim();
                            building = roomMatch[2] ? roomMatch[2].trim() : '';
                        }
                    }

                    records.push({
                        day: dayName,
                        dayIndex,
                        slot: slotIdx + 1,
                        time: item.time || '',
                        subject: item.subject || '',
                        discipline: item.subject || '',
                        type: item.lessonType || '',
                        lessonType: item.lessonType || '',
                        teacher: teacherName,
                        group: item.group || '',
                        room,
                        building
                    });
                }
            }
        }
        return records;
    }

    /**
     * Резервное получение расписания из MongoDB при исчерпании лимита API (110 req/min)
     */
    async fetchScheduleFromDb(params = {}, cacheType = null) {
        const isTeacher = cacheType === 'teacher' || !!params.teacher || !!params.q;

        try {
            if (isTeacher) {
                const teacherQuery = params.teacher || params.q;
                if (teacherQuery || params.teacherId) {
                    const { Teacher } = await import("../models/teacher.js");
                    const { TeacherSchedule } = await import("../models/teacherSchedule.js");

                    let teacherDoc = null;
                    if (params.teacherId) {
                        teacherDoc = await Teacher.findOne({ id: Number(params.teacherId) }).lean().catch(() => null);
                    }

                    if (!teacherDoc && teacherQuery) {
                        const cleanName = this.cleanTeacherName(teacherQuery);
                        const surname = cleanName.split(/[\s.]+/).filter(Boolean)[0] || cleanName;
                        const teacherDocs = await Teacher.find({ name: new RegExp(surname, 'i') }).lean().catch(() => []);
                        if (teacherDocs.length > 0) {
                            teacherDoc = teacherDocs.find(t => this.matchTeacherRecord(t.name, teacherQuery)) || teacherDocs[0];
                        }
                    }

                    if (teacherDoc) {
                        const dbDoc = await TeacherSchedule.findOne({ teacherId: teacherDoc.id }).lean().catch(() => null);
                        const daysData = dbDoc?.data || dbDoc?.schedule;
                        if (Array.isArray(daysData)) {
                            const records = this._dbTeacherScheduleToRecords(daysData, teacherDoc.name);
                            return { records, total: records.length, _fromDb: true };
                        }
                    }
                }
            } else {
                const groupName = params.group;
                if (groupName || params.groupId) {
                    const { Group } = await import("../models/group.js");
                    const { Schedule } = await import("../models/schedule.js");

                    let groupDoc = null;
                    if (params.groupId) {
                        groupDoc = await Group.findOne({ id: Number(params.groupId) }).lean().catch(() => null);
                    }

                    if (!groupDoc && groupName) {
                        const escaped = groupName.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&');
                        groupDoc = await Group.findOne({ name: new RegExp(`^${escaped}$`, 'i') }).lean().catch(() => null);
                        if (!groupDoc) {
                            groupDoc = await Group.findOne({ name: new RegExp(escaped, 'i') }).lean().catch(() => null);
                        }
                    }

                    if (groupDoc) {
                        const dbDoc = await Schedule.findOne({ groupId: groupDoc.id }).lean().catch(() => null);
                        const daysData = dbDoc?.data || dbDoc?.schedule;
                        if (Array.isArray(daysData)) {
                            const records = this._dbScheduleToRecords(daysData, groupDoc.name);
                            return { records, total: records.length, _fromDb: true };
                        }
                    }
                }
            }
        } catch (e) {
            log.warn(`[BuketovApiService] Ошибка fetchScheduleFromDb: ${e.message}`);
        }

        return { records: [], total: 0, _fromDb: true };
    }

    /**
     * Публичный интерфейс запроса расписания (делегирует в fetchScheduleFromApi)
     */
    async fetchSchedule(params = {}, cacheType = null) {
        return this.fetchScheduleFromApi(params, cacheType);
    }

    /**
     * Выполнение запроса к /api/v1/schedule с защитным лимитом 110 req/min, RAM-кэшем и Circuit Breaker
     */
    async fetchScheduleFromApi(params = {}, cacheType = null) {
        // Определяем изолированный партиционированный кэш
        const isTeacher = cacheType === 'teacher' || !!params.teacher || !!params.q;
        const targetCache = isTeacher ? this.teacherCache : this.studentCache;
        const targetCapacity = isTeacher ? this.TEACHER_CAPACITY : this.STUDENT_CAPACITY;

        const url = new URL(config.SCHEDULE_API_URL);
        for (const [key, val] of Object.entries(params)) {
            if (val !== undefined && val !== null && val !== '') {
                url.searchParams.set(key, String(val));
            }
        }

        const cacheKey = url.toString();
        const cached = this._getCache(targetCache, cacheKey);
        const now = Date.now();

        // 1. Мгновенная отдача из локального RAM-кэша (0 сетевых запросов при свежем кэше)
        if (cached && (now - cached.timestamp < this.cacheTTL)) {
            return cached.data;
        }

        // 2. Проверка Circuit Breaker: если сайт КарУ лежит, мгновенно отдаем fallback без лагов сети
        if (this.circuitState === 'API_DOWN') {
            if (now - this.circuitTrippedAt < this.CIRCUIT_COOLDOWN_MS) {
                // Еще идет 2-минутный кулдаун
                if (cached && cached.data) {
                    log.warn(`[BuketovApiService] Circuit Breaker OPEN (API_DOWN). Использован локальный RAM-кэш для ${cacheKey}.`);
                    return cached.data;
                }
                const circuitError = new Error(`Circuit Breaker OPEN (API_DOWN). Сервер КарУ временно недоступен.`);
                circuitError.isCircuitBreaker = true;
                circuitError.status = 503;
                throw circuitError;
            } else {
                // 2 минуты прошло: переход в HALF_OPEN для проверки восстановления сервера
                this.circuitState = 'HALF_OPEN';
                log.info(`[BuketovApiService] Circuit Breaker: 2 минуты прошло, переход в HALF_OPEN (тестовый запрос к API).`);
            }
        }

        // 3. Железный предохранитель 110 req/min (Outbound Sliding Window Rate Limiter)
        if (!this.checkRateLimit()) {
            log.warn(`[BuketovApiService] ⚠️ Достигнут защитный лимит 110 запросов/мин (максимум API 120). Временный fallback на MongoDB.`);
            return await this.fetchScheduleFromDb(params, cacheType);
        }

        // Фиксируем временную метку разрешенного сетевого вызова
        this.outboundRequestTimestamps.push(Date.now());

        const headers = {
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip'
        };

        if (config.SCHEDULE_API_KEY) {
            headers['Authorization'] = `Bearer ${config.SCHEDULE_API_KEY}`;
        }

        if (cached && cached.etag) {
            headers['If-None-Match'] = cached.etag;
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), this.REQUEST_TIMEOUT_MS);

        try {
            const response = await fetch(url, {
                method: 'GET',
                headers,
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            // 304 Not Modified — продлеваем время жизни кэша и отдаем закэшированные данные
            if (response.status === 304 && cached) {
                this._recordSuccess();
                cached.timestamp = Date.now();
                this._setCache(targetCache, targetCapacity, cacheKey, cached);
                return cached.data;
            }

            if (!response.ok) {
                const errText = await response.text().catch(() => '');
                const err = new Error(`Buketov API error [HTTP ${response.status}]: ${errText}`);
                err.status = response.status;
                throw err;
            }

            const data = await response.json();
            const etag = response.headers.get('etag') || '';

            // Успех — сбрасываем счетчик сбоев Circuit Breaker
            this._recordSuccess();

            // Сохраняем в изолированный партиционированный LRU кэш
            this._setCache(targetCache, targetCapacity, cacheKey, {
                etag,
                data,
                timestamp: Date.now()
            });

            return data;
        } catch (e) {
            clearTimeout(timeoutId);

            // Сетевые ошибки, таймаут AbortError и 5xx регистрируются как сбой в Circuit Breaker
            if (!e.status || e.status >= 500) {
                this._recordFailure(e);
            }

            // Маскируем авторизацию при логировании ошибки
            log.error(`[BuketovApiService] Ошибка запроса к API (${params.group || params.teacher || params.q}): ${e.message}`);

            // Резервный возврат: если сеть дала сбой, отдаем кэш, даже если он старше 10 минут
            if (cached && cached.data) {
                log.warn(`[BuketovApiService] Использован резервный локальный кэш для ${cacheKey} из-за сбоя сети`);
                return cached.data;
            }
            throw e;
        }
    }

    /**
     * Получение полного расписания группы с авто-пагинацией
     */
    async getGroupSchedule(groupName) {
        if (!groupName) throw new Error("Имя группы обязательно для запроса");

        let allRecords = [];
        let offset = 0;
        const limit = 200;
        let result = null;

        while (true) {
            result = await this.fetchSchedule({
                group: groupName,
                limit,
                offset
            }, 'student');

            if (result && Array.isArray(result.records)) {
                allRecords.push(...result.records);
            }

            if (result?.pagination?.hasMore && result.pagination.nextOffset) {
                offset = result.pagination.nextOffset;
            } else {
                break;
            }
        }

        return {
            ...result,
            records: allRecords
        };
    }

    /**
     * Проверка соответствия записи расписания конкретному преподавателю
     * Учитывает фамилию и первую букву инициала, независимо от пробелов, точек и казахских букв
     */
    matchTeacherRecord(recordTeacher, targetName) {
        if (!recordTeacher || !targetName) return false;
        const cleanTarget = normalizeCyrillic(this.cleanTeacherName(targetName));
        const cleanRecord = normalizeCyrillic(this.cleanTeacherName(recordTeacher));

        const targetParts = cleanTarget.split(' ').filter(Boolean);
        const recordParts = cleanRecord.split(' ').filter(Boolean);

        const targetSurname = targetParts[0];
        const recordSurname = recordParts[0];

        if (targetSurname !== recordSurname) return false;

        const targetInitial = targetParts[1]?.[0];
        const recordInitial = recordParts[1]?.[0];

        if (targetInitial && recordInitial && targetInitial !== recordInitial) return false;
        return true;
    }

    /**
     * Получение полного расписания преподавателя
     */
    async getTeacherSchedule(teacherName) {
        if (!teacherName) throw new Error("ФИО преподавателя обязательно для запроса");

        const cleanName = this.cleanTeacherName(teacherName);
        const surname = cleanName.split(/[\s.]+/).filter(Boolean)[0] || cleanName;
        let allRecords = [];
        let offset = 0;
        const limit = 200;
        let result = null;
        let pageCount = 0;
        const maxPages = 5;

        while (pageCount < maxPages) {
            pageCount++;
            result = await this.fetchSchedule({
                q: surname,
                limit,
                offset
            }, 'teacher');

            if (result && Array.isArray(result.records)) {
                // Фильтруем записи по ФИО преподавателя
                const matched = result.records.filter(r => this.matchTeacherRecord(r.teacher, teacherName));
                allRecords.push(...matched);
            }

            if (result?.pagination?.hasMore && result.pagination.nextOffset) {
                offset = result.pagination.nextOffset;
            } else {
                break;
            }
        }

        return {
            ...result,
            records: allRecords
        };
    }
}

export default new BuketovApiService();
