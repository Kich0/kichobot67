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

                    const compFull = normFull.replace(/\s+/g, '');

                    // 2. Абсолютное точное совпадение по полному ФИО (с пробелами или без пробелов)
                    if (normFull === normQuery || compFull === compactQuery || normName === normQuery) {
                        matches.push({ teacher: t, score: 200 });
                        continue;
                    }

                    // 3. Точное совпадение по фамилии
                    if (normLast === normQuery) {
                        matches.push({ teacher: t, score: 150 });
                        continue;
                    }

                    // 4. Точное совпадение по имени (например: "Нурсултан", "Надежда", "Еркин")
                    if (normFirst && normFirst === normQuery) {
                        matches.push({ teacher: t, score: 140 });
                        continue;
                    }

                    // 5. Совпадение по псевдонимам (aliases)
                    if (t.aliases && Array.isArray(t.aliases)) {
                        const aliasMatched = t.aliases.some(alias => {
                            const normAlias = normalizeCyrillic(alias);
                            return normAlias === normQuery || normAlias.replace(/\s+/g, '') === compactQuery || normAlias.startsWith(normQuery) || (tokens.length > 1 && normAlias.includes(normQuery));
                        });
                        if (aliasMatched) {
                            matches.push({ teacher: t, score: 135 });
                            continue;
                        }
                    }

                    // 6. Многословный запрос: все слова запроса точно входят в ФИО (в любом порядке!)
                    // Например: "Еркін Сағынтайұлы Аяған", "Надежда Попова", "Попова Надежда"
                    if (tokens.length > 1 && tokens.every(qTok => fullTokens.includes(qTok))) {
                        matches.push({ teacher: t, score: 130 });
                        continue;
                    }

                    // 7. Фамилия начинается с запроса (например: "попов" -> "Попова")
                    if (normLast.startsWith(normQuery)) {
                        matches.push({ teacher: t, score: 110 });
                        continue;
                    }

                    // 8. Имя начинается с запроса (например: "салтанат", "айнур", "полина")
                    if (normFirst && normFirst.startsWith(normQuery)) {
                        matches.push({ teacher: t, score: 100 });
                        continue;
                    }

                    // 9. Многословный запрос: каждый токен запроса является префиксом слова в ФИО (в любом порядке!)
                    if (tokens.length > 1) {
                        const allTokensPrefixMatch = tokens.every(qTok => fullTokens.some(w => w.startsWith(qTok)));
                        if (allTokensPrefixMatch) {
                            matches.push({ teacher: t, score: 95 });
                            continue;
                        }
                    }

                    // 10. Отчество начинается с запроса (например: "камелович", "викторовна")
                    if (normPatr && normPatr.startsWith(normQuery)) {
                        matches.push({ teacher: t, score: 90 });
                        continue;
                    }

                    // 11. Инициалы начинаются с запроса (например: "попова н" -> "Попова Н. В.")
                    if (normName.startsWith(normQuery) || normName.replace(/\s+/g, '').startsWith(compactQuery)) {
                        matches.push({ teacher: t, score: 85 });
                        continue;
                    }

                    // 12. Опечатка в 1 букву в фамилии (длина запроса и фамилии >= 4)
                    // Например: "Попово" -> "Попова", "Аягон" -> "Аяған"
                    if (tokens.length === 1 && normQuery.length >= 4 && normLast.length >= 4) {
                        if (levenshteinDistance(normLast, normQuery) === 1) {
                            matches.push({ teacher: t, score: 84 });
                            continue;
                        }
                    }

                    // 13. Опечатка в 1 букву в имени (длина >= 4)
                    // Например: "Еркен" -> "Еркін"
                    if (tokens.length === 1 && normQuery.length >= 4 && normFirst.length >= 4) {
                        if (levenshteinDistance(normFirst, normQuery) === 1) {
                            matches.push({ teacher: t, score: 82 });
                            continue;
                        }
                    }

                    // 14. Многословный запрос с опечаткой в 1 букву суммарно
                    // Например: "Попова Надежда Виктаровна", "Аяган Еркин Сагинтайулы", "Жунусава Баян"
                    if (tokens.length > 1) {
                        let totalTypos = 0;
                        let allTokensMatch = true;

                        for (const qTok of tokens) {
                            const exactPrefix = fullTokens.some(w => w.startsWith(qTok));
                            if (exactPrefix) continue;

                            if (qTok.length >= 4) {
                                const typoMatch = fullTokens.some(w => {
                                    for (const len of [qTok.length - 1, qTok.length, qTok.length + 1]) {
                                        if (len >= 3 && len <= w.length) {
                                            if (levenshteinDistance(w.slice(0, len), qTok) <= 1) return true;
                                        }
                                    }
                                    return false;
                                });
                                if (typoMatch) {
                                    totalTypos++;
                                    continue;
                                }
                            }

                            allTokensMatch = false;
                            break;
                        }

                        if (allTokensMatch && totalTypos <= 1) {
                            matches.push({ teacher: t, score: 78 });
                            continue;
                        }
                    }

                    // 15. Компактное совпадение от 3 букв (без пробелов)
                    if (compactQuery.length >= 3 && compFull.startsWith(compactQuery)) {
                        matches.push({ teacher: t, score: 72 });
                        continue;
                    }

                    // 16. Компактное совпадение от 6 букв с 1 опечаткой
                    if (compactQuery.length >= 6) {
                        const prefix = compFull.slice(0, compactQuery.length);
                        if (levenshteinDistance(prefix, compactQuery) === 1) {
                            matches.push({ teacher: t, score: 68 });
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

export function levenshteinDistance(s1, s2) {
    const a = s1.toLowerCase();
    const b = s2.toLowerCase();
    const matrix = [];
    for (let i = 0; i <= b.length; i++) matrix[i] = [i];
    for (let j = 0; j <= a.length; j++) matrix[0][j] = j;

    for (let i = 1; i <= b.length; i++) {
        for (let j = 1; j <= a.length; j++) {
            if (b.charAt(i - 1) === a.charAt(j - 1)) {
                matrix[i][j] = matrix[i - 1][j - 1];
            } else {
                matrix[i][j] = Math.min(
                    matrix[i - 1][j - 1] + 1,
                    matrix[i][j - 1] + 1,
                    matrix[i - 1][j] + 1
                );
            }
        }
    }
    return matrix[b.length][a.length];
}

export default new teacherService()