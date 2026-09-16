import {Teacher} from "../models/teacher.js";
import log from "../logging/logging.js";

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
                    uniqueTeachers.push(t);

                    idMap.set(Number(t.id), t);
                    idMap.set(String(t.id), t);

                    if (t.department !== undefined && t.department !== null) {
                        const deptKey = Number(t.department);
                        let list = deptMap.get(deptKey);
                        if (!list) {
                            list = [];
                            deptMap.set(deptKey, list);
                        }
                        list.push(t);
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
            return await Teacher.findOne({id}).lean();
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
            });
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
            });
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
                    uniqueTeachers.push(t);
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
            return res;
        } catch (e) {
            throw new Error("Ошибка при обновлении всех Teacher: " + e.stack);
        }
    }

    findByName = async (name) => {
        try {
            await this._ensureCache();

            if (this._cache && this._cache.length > 0) {
                const cleanQuery = String(name || '').replace(/[-_.,]/g, ' ').toLowerCase().trim();
                const tokens = cleanQuery.split(/\s+/).filter(Boolean);

                if (tokens.length === 0) return [...this._cache];

                const results = this._cache.filter(t => {
                    if (!t || !t.name) return false;
                    const lower = t.name.toLowerCase();
                    return tokens.every(tok => lower.includes(tok));
                });
                return results;
            }

            // Fallback в MongoDB
            const regExp = new RegExp(name, "i");
            const docs = await Teacher.find({name: {$regex: regExp}}).sort('name').lean();
            const seen = new Set();
            return docs.filter(t => {
                if (seen.has(t.id)) return false;
                seen.add(t.id);
                return true;
            });
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