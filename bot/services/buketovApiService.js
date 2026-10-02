import config from "../../config.js";
import log from "../logging/logging.js";
import { cleanTitles, normalizeCyrillic } from "./teacherDirectoryService.js";

class BuketovApiService {
    constructor() {
        // Раздельные LRU-кэши в оперативной памяти:
        // 1. studentCache — 400 слотов под группы студентов (~8 МБ)
        this.studentCache = new Map();
        this.STUDENT_CAPACITY = 400;

        // 2. teacherCache — 100 слотов под преподавателей (~2.5 МБ)
        // Трафик студентов НИКОГДА не вытесняет расписание преподавателей!
        this.teacherCache = new Map();
        this.TEACHER_CAPACITY = 100;

        this.cacheTTL = 10 * 60 * 1000; // 10 минут локального кэша

        // Circuit Breaker (Автоматический предохранитель)
        this.circuitState = 'CLOSED'; // 'CLOSED' | 'API_DOWN' | 'HALF_OPEN'
        this.consecutiveFailures = 0;
        this.circuitTrippedAt = 0;
        this.CIRCUIT_COOLDOWN_MS = 2 * 60 * 1000; // 2 минуты в состоянии API_DOWN
        this.FAILURE_THRESHOLD = 2; // 2 подряд сбоя -> трип
        this.REQUEST_TIMEOUT_MS = 4500; // 4.5 секунды таймаут запроса (AbortController)

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
     * Очистка звания преподавателя для точного совпадения в API
     * Например: "ст.преп. Попова Н. В." -> "Попова Н. В."
     */
    cleanTeacherName(rawName) {
        if (!rawName) return '';
        return cleanTitles(rawName);
    }

    /**
     * Выполнение запроса к /api/v1/schedule с разделенным RAM-кэшем и Circuit Breaker
     */
    async fetchSchedule(params = {}, cacheType = null) {
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
