import config from "../../config.js";
import log from "../logging/logging.js";

class BuketovApiService {
    constructor() {
        this.cache = new Map(); // key -> { etag, data, timestamp }
        this.cacheTTL = 1 * 60 * 1000; // 1 минута локального кэша (Near Real-Time)
    }

    /**
     * Очистка звания преподавателя для точного совпадения в API
     * Например: "аcсис.проф. Омаров М." -> "Омаров М."
     */
    cleanTeacherName(rawName) {
        if (!rawName) return '';
        return rawName
            .replace(/^(аc?соц\.?\s*проф\.?|аc?сис\.?\s*проф\.?|ст\.?\s*пр\.?|пр\.?|проф\.?|доц\.?|преп\.?|м\.т\.ғ\.к\.?|п\.ғ\.к\.?)\s+/iu, '')
            .trim();
    }

    /**
     * Выполнение запроса к /api/v1/schedule
     */
    async fetchSchedule(params = {}) {
        const url = new URL(config.SCHEDULE_API_URL);
        
        for (const [key, val] of Object.entries(params)) {
            if (val !== undefined && val !== null && val !== '') {
                url.searchParams.set(key, String(val));
            }
        }

        const cacheKey = url.toString();
        const cached = this.cache.get(cacheKey);

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

        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 15000); // 15 сек таймаут

            const response = await fetch(url, {
                method: 'GET',
                headers,
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            // 304 Not Modified — отдаем закэшированные данные
            if (response.status === 304 && cached) {
                return cached.data;
            }

            if (!response.ok) {
                const errText = await response.text().catch(() => '');
                throw new Error(`Buketov API error [HTTP ${response.status}]: ${errText}`);
            }

            const data = await response.json();
            const etag = response.headers.get('etag') || '';

            // Сохраняем в локальный кэш
            this.cache.set(cacheKey, {
                etag,
                data,
                timestamp: Date.now()
            });

            // Очистка старого кэша (если больше 500 записей)
            if (this.cache.size > 500) {
                const now = Date.now();
                for (const [k, v] of this.cache.entries()) {
                    if (now - v.timestamp > this.cacheTTL) {
                        this.cache.delete(k);
                    }
                }
            }

            return data;
        } catch (e) {
            // Маскируем авторизацию при логировании ошибки
            log.error(`[BuketovApiService] Ошибка запроса к API (${params.group || params.teacher || params.q}): ${e.message}`);
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
            });

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
     * Учитывает фамилию и первую букву инициала, независимо от пробелов и точек
     */
    matchTeacherRecord(recordTeacher, targetName) {
        if (!recordTeacher || !targetName) return false;
        const cleanTarget = this.cleanTeacherName(targetName);
        const targetParts = cleanTarget.split(/[\s.]+/).filter(Boolean);
        const targetSurname = targetParts[0]?.toLowerCase();
        const targetInitial = targetParts[1]?.[0]?.toLowerCase();

        const recordParts = recordTeacher.split(/[\s.]+/).filter(Boolean);
        const recordSurname = recordParts[0]?.toLowerCase();
        const recordInitial = recordParts[1]?.[0]?.toLowerCase();

        if (targetSurname !== recordSurname) return false;
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
            });

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
