import {Teacher} from "../models/teacher.js";
import log from "../logging/logging.js";
import teacherDirectoryService, { normalizeCyrillic } from "./teacherDirectoryService.js";

class teacherService {
    constructor() {
        this._cache = null;
        this._idMap = new Map();
        this._deptMap = new Map();
        this._lastCacheTime = 0;
        this._ttl = 60 * 60 * 1000; // 1 час
        this._loadingPromise = null;
    }

    _ensureCache = async () => {
        const now = Date.now();
        if (this._cache && (now - this._lastCacheTime < this._ttl) && this._cache.length > 0) {
            return this._cache;
        }

        // Предотвращение одновременных параллельных запросов к БД при прогреве
        if (this._loadingPromise) {
            return await this._loadingPromise;
        }

        this._loadingPromise = (async () => {
            try {
                const docs = await Teacher.find({}).sort('name').lean();
                const seen = new Set();
                const uniqueTeachers = [];
                const idMap = new Map();
                const deptMap = new Map();

                for (const t of docs) {
                    if (!t || !t.id || seen.has(t.id)) continue;
                    seen.add(t.id);
                    const enriched = teacherDirectoryService.enrich(t);
                    uniqueTeachers.push(enriched);

                    idMap.set(Number(t.id), enriched);
                    idMap.set(String(t.id), enriched);

                    if (t.department !== undefined && t.department !== null) {
                        const deptKey = Number(t.department);
                        let list = deptMap.get(deptKey);
                        if (!list) {
                            list = [];
                            deptMap.set(deptKey, list);
                        }
                        list.push(enriched);
                    }
                }

                this._cache = uniqueTeachers;
                this._idMap = idMap;
                this._deptMap = deptMap;
                this._lastCacheTime = Date.now();
                return this._cache;
            } catch (e) {
                log.error("[TeacherService] Ошибка загрузки кэша учителей: " + e.message);
                if (this._cache) return this._cache;
                throw e;
            } finally {
                this._loadingPromise = null;
            }
        })();

        return await this._loadingPromise;
    }

    invalidateCache = () => {
        this._cache = null;
        this._idMap.clear();
        this._deptMap.clear();
        this._lastCacheTime = 0;
    }

    getById = async (id) => {
        try {
            if (id === undefined || id === null) return null;

            // 1. Проверяем instant in-memory Map O(1)
            const cached = this._idMap.get(Number(id)) || this._idMap.get(String(id));
            if (cached) return cached;

            // 2. Если кэш ещё не прогрет — прогреваем
            await this._ensureCache();
            const recheck = this._idMap.get(Number(id)) || this._idMap.get(String(id));
            if (recheck) return recheck;

            // 3. Fallback в MongoDB на случай редкого ID
            const doc = await Teacher.findOne({id}).lean();
            return doc ? teacherDirectoryService.enrich(doc) : null;
        } catch (e) {
            throw new Error("Ошибка при получении Teacher по айди: " + e.stack)
        }
    }

    getByDepartmentId = async (departmentId) => {
        try {
            await this._ensureCache();
            const list = this._deptMap.get(Number(departmentId));
            if (list) return [...list];

            // Fallback в MongoDB
            const docs = await Teacher.find({department: departmentId}).sort('name').lean();
            const seen = new Set();
            return docs.filter(t => {
                if (seen.has(t.id)) return false;
                seen.add(t.id);
                return true;
            }).map(t => teacherDirectoryService.enrich(t));
        } catch (e) {
            throw new Error("Ошибка при получении Teacher по DepartmentId: " + e.stack)
        }
    }

    getAll = async () => {
        try {
            await this._ensureCache();
            if (this._cache && this._cache.length > 0) {
                return [...this._cache];
            }
            const docs = await Teacher.find({}).sort('name').lean();
            const seen = new Set();
            return docs.filter(t => {
                if (seen.has(t.id)) return false;
                seen.add(t.id);
                return true;
            }).map(t => teacherDirectoryService.enrich(t));
        } catch (e) {
            throw new Error("Ошибка при получении всех Teacher: " + e.stack)
        }
    }

    updateAll = async (teachers) => {
        try {
            if (!teachers || teachers.length === 0) return null;

            // Дедупликация в памяти перед записью в БД
            const seen = new Set();
            const uniqueTeachers = [];
            for (const t of teachers) {
                if (t && t.id && !seen.has(t.id)) {
                    seen.add(t.id);
                    const enriched = teacherDirectoryService.enrich(t);
                    uniqueTeachers.push(enriched);
                }
            }

            // Атомарный upsert без удаления (zero-downtime, без дубликатов)
            const operations = uniqueTeachers.map(teacher => ({
                updateOne: {
                    filter: { id: teacher.id },
                    update: { $set: teacher },
                    upsert: true
                }
            }));

            const res = await Teacher.bulkWrite(operations, { ordered: false });
            this.invalidateCache();
            await this._ensureCache().catch(() => {});
            log.info(`[TeacherService] Обновление преподавателей завершено: добавлено новых: ${res.upsertedCount || 0}, обновлено: ${res.modifiedCount || 0}, всего уникальных: ${uniqueTeachers.length}`);
            return res;
        } catch (e) {
            throw new Error("Ошибка при обновлении всех Teacher: " + e.stack);
        }
    }

    findByName = async (name) => {
        try {
            await this._ensureCache();

            if (this._cache && this._cache.length > 0) {
                const normQuery = normalizeCyrillic(name);
                if (!normQuery) return [...this._cache];

                const tokens = normQuery.split(' ').filter(Boolean);
                const compactQuery = normQuery.replace(/\s+/g, '');

                if (tokens.length === 0) return [...this._cache];

                const matches = [];

                for (const t of this._cache) {
                    if (!t || !t.name) continue;

                    const normName = normalizeCyrillic(t.name);
                    const normFull = normalizeCyrillic(t.fullName || t.name);
                    const normLast = normalizeCyrillic(t.lastName || t.name.split(' ')[0]);
                    const normFirst = normalizeCyrillic(t.firstName || '');
                    const normPatr = normalizeCyrillic(t.patronymic || '');
                    const words = [normLast, normFirst, normPatr].filter(Boolean);
                    const fullTokens = normFull.split(' ').filter(Boolean);

                    // 1. Короткие запросы (<= 2 символов, например "на", "ис")
                    // СТРОГО префикс фамилии или имени! Никаких случайных совпадений в отчествах!
                    if (tokens.length === 1 && normQuery.length <= 2) {
                        const lastStarts = normLast.startsWith(normQuery);
                        const firstStarts = normFirst && normFirst.startsWith(normQuery);
                        if (lastStarts || firstStarts) {
                            matches.push({
                                teacher: t,
                                score: lastStarts ? 100 : 60
                            });
                        }
                        continue;
                    }

                    // 2. Точное совпадение по фамилии
                    if (normLast === normQuery) {
                        matches.push({ teacher: t, score: 120 });
                        continue;
                    }

                    // 3. Фамилия начинается с запроса (например: "попов" -> "Попова")
                    if (normLast.startsWith(normQuery)) {
                        matches.push({ teacher: t, score: 100 });
                        continue;
                    }

                    // 4. Имя начинается с запроса (например: "салтанат", "айнур", "полина")
                    if (normFirst && normFirst.startsWith(normQuery)) {
                        matches.push({ teacher: t, score: 85 });
                        continue;
                    }

                    // 5. Инициалы начинаются с запроса (например: "попова н" -> "Попова Н. В.")
                    if (normName.startsWith(normQuery) || normName.replace(/\s+/g, '').startsWith(compactQuery)) {
                        matches.push({ teacher: t, score: 90 });
                        continue;
                    }

                    // 6. Многословный запрос (например: "Попова Надежда", "Танин Алибек", "Надежда Викторовна")
                    // Каждый токен запроса должен быть префиксом хотя бы одного слова в ФИО
                    if (tokens.length > 1) {
                        const allTokensMatch = tokens.every(qTok => fullTokens.some(w => w.startsWith(qTok)));
                        if (allTokensMatch) {
                            matches.push({ teacher: t, score: 80 });
                            continue;
                        }
                    }

                    // 7. Компактное совпадение от 3 букв (без пробелов)
                    if (compactQuery.length >= 3) {
                        const compFull = normFull.replace(/\s+/g, '');
                        if (compFull.startsWith(compactQuery)) {
                            matches.push({ teacher: t, score: 70 });
                            continue;
                        }
                    }
                }

                // Сортировка по релевантности:
                // Наивысший балл -> Заведующие кафедрой -> Алфавит
                matches.sort((a, b) => {
                    if (b.score !== a.score) return b.score - a.score;
                    if (b.teacher.isHead !== a.teacher.isHead) return (b.teacher.isHead ? 1 : 0) - (a.teacher.isHead ? 1 : 0);
                    const nameA = a.teacher.fullName || a.teacher.name;
                    const nameB = b.teacher.fullName || b.teacher.name;
                    return nameA.localeCompare(nameB, 'ru');
                });

                return matches.map(m => m.teacher);
            }

            // Fallback в MongoDB
            const escaped = String(name || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const regExp = new RegExp(escaped, "i");
            const docs = await Teacher.find({name: {$regex: regExp}}).sort('name').lean();
            const seen = new Set();
            return docs.filter(t => {
                if (seen.has(t.id)) return false;
                seen.add(t.id);
                return true;
            }).map(t => teacherDirectoryService.enrich(t));
        } catch (e) {
            throw new Error("Ошибка при поиске Teacher по имени: " + e.stack);
        }
    }

    deduplicateTeachers = async () => {
        try {
            const dups = await Teacher.aggregate([
                { $group: { _id: '$id', count: { $sum: 1 }, ids: { $push: '$_id' } } },
                { $match: { count: { $gt: 1 } } }
            ]);

            let deletedCount = 0;
            for (const dup of dups) {
                const [keep, ...removeIds] = dup.ids;
                if (removeIds.length > 0) {
                    const res = await Teacher.deleteMany({ _id: { $in: removeIds } });
                    deletedCount += res.deletedCount || 0;
                }
            }
            if (deletedCount > 0) {
                this.invalidateCache();
                await this._ensureCache().catch(() => {});
            }
            return deletedCount;
        } catch (e) {
            throw new Error("Ошибка при дедупликации учителей: " + e.stack);
        }
    }
}

export default new teacherService()