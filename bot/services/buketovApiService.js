import config from "../../config.js";
import log from "../logging/logging.js";
import { cleanTitles, normalizeCyrillic } from "./teacherDirectoryService.js";

class BuketovApiService {
    constructor() {
        this.cache = new Map(); // key -> { etag, data, timestamp }
        this.cacheTTL = 10 * 60 * 1000; // 10 минут локального кэша
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
        const now = Date.now();

        // 1. Мгновенная отдача из локального RAM-кэша (0 сетевых запросов при свежем кэше)
        if (cached && (now - cached.timestamp < this.cacheTTL)) {
            return cached.data;
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

        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 15000); // 15 сек таймаут

            const response = await fetch(url, {
                method: 'GET',
                headers,
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            // 304 Not Modified — продлеваем время жизни кэша и отдаем закэшированные данные
            if (response.status === 304 && cached) {
                cached.timestamp = Date.now();
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

            // Очистка старого кэша (если больше 250 записей)
            if (this.cache.size > 250) {
                const currentTime = Date.now();
                for (const [k, v] of this.cache.entries()) {
                    if (currentTime - v.timestamp > this.cacheTTL) {
                        this.cache.delete(k);
                    }
                }
            }

            return data;
        } catch (e) {
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
