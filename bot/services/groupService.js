import {Group} from "../models/group.js";
import log from "../logging/logging.js";
import { normalizeCyrillic } from "./teacherDirectoryService.js";

class groupService {
    constructor() {
        this._cache = null;
        this._idMap = new Map();
        this._programMap = new Map();
        this._lastCacheTime = 0;
        this._ttl = 60 * 60 * 1000; // 1 час
        this._loadingPromise = null;
    }

    _ensureCache = async () => {
        const now = Date.now();
        if (this._cache && (now - this._lastCacheTime < this._ttl) && this._cache.length > 0) {
            return this._cache;
        }

        if (this._loadingPromise) {
            return await this._loadingPromise;
        }

        this._loadingPromise = (async () => {
            try {
                const docs = await Group.find({}).sort('-id').lean();
                const seen = new Set();
                const uniqueGroups = [];
                const idMap = new Map();
                const programMap = new Map();

                for (const g of docs) {
                    if (!g || !g.id || seen.has(g.id)) continue;
                    seen.add(g.id);
                    uniqueGroups.push(g);

                    idMap.set(Number(g.id), g);
                    idMap.set(String(g.id), g);

                    if (g.program !== undefined && g.program !== null) {
                        const progKey = Number(g.program);
                        let list = programMap.get(progKey);
                        if (!list) {
                            list = [];
                            programMap.set(progKey, list);
                        }
                        list.push(g);
                    }
                }

                this._cache = uniqueGroups;
                this._idMap = idMap;
                this._programMap = programMap;
                this._lastCacheTime = Date.now();
                return this._cache;
            } catch (e) {
                log.error("[GroupService] Ошибка загрузки кэша групп: " + e.message);
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
        this._programMap.clear();
        this._lastCacheTime = 0;
    }

    getByProgramId = async (programId) => {
        try {
            await this._ensureCache();
            const list = this._programMap.get(Number(programId));
            if (list) return [...list];

            const docs = await Group.find({program: programId}).sort('-id').lean();
            const seen = new Set();
            return docs.filter(g => {
                if (seen.has(g.id)) return false;
                seen.add(g.id);
                return true;
            });
        } catch (e) {
            throw new Error("Ошибка при получении группы по програмАйди: " + e.stack)
        }
    }

    getById = async (id) => {
        try {
            if (id === undefined || id === null) return null;

            // Instant in-memory Map O(1)
            const cached = this._idMap.get(Number(id)) || this._idMap.get(String(id));
            if (cached) return cached;

            await this._ensureCache();
            const recheck = this._idMap.get(Number(id)) || this._idMap.get(String(id));
            if (recheck) return recheck;

            return await Group.findOne({id}).lean();
        } catch (e) {
            throw new Error("Ошибка при получении группы по айди: " + e.stack)
        }
    }

    getAll = async () => {
        try {
            await this._ensureCache();
            if (this._cache && this._cache.length > 0) {
                return [...this._cache];
            }
            return await Group.find({}).lean();
        } catch (e) {
            throw new Error("Ошибка при получении всех групп: " + e.stack)
        }
    }

    updateAll = async (groups, removeStale = false) => {
        try {
            if (!groups || groups.length === 0) return null;

            // Дедупликация в памяти перед записью
            const seen = new Set();
            const uniqueGroups = [];
            for (const g of groups) {
                if (g && g.id && !seen.has(g.id)) {
                    seen.add(g.id);
                    uniqueGroups.push(g);
                }
            }

            // Атомарный upsert по уникальному ID группы (zero-downtime)
            const operations = uniqueGroups.map(group => ({
                updateOne: {
                    filter: { id: group.id },
                    update: { $set: group },
                    upsert: true
                }
            }));
            const res = await Group.bulkWrite(operations, { ordered: false });

            // Удаляем старые группы ТОЛЬКО при полном синке всего университета (>= 500 групп)
            if (removeStale || uniqueGroups.length >= 500) {
                const activeIds = uniqueGroups.map(g => g.id).filter(Boolean);
                if (activeIds.length > 0) {
                    await Group.deleteMany({ id: { $nin: activeIds } });
                }
            }

            this.invalidateCache();
            await this._ensureCache().catch(() => {});
            return res;
        } catch (e) {
            throw new Error("Ошибка при обновлении всех групп: " + e.stack)
        }
    }

    syncProgramGroups = async (programId) => {
        try {
            const { default: ScheduleService } = await import("../../backend/services/ScheduleService.js");
            const rawGroups = await ScheduleService.get_group_list_by_programId(programId);
            if (!rawGroups || rawGroups.length === 0) {
                return await this.getByProgramId(programId);
            }

            // Атомарный upsert по уникальному ID группы
            const operations = rawGroups.map(group => ({
                updateOne: {
                    filter: { id: group.id },
                    update: { $set: {
                        name: group.name,
                        id: group.id,
                        language: group.language,
                        href: group.href,
                        age: group.age,
                        studentCount: group.studentCount,
                        program: Number(programId)
                    } },
                    upsert: true
                }
            }));
            await Group.bulkWrite(operations, { ordered: false });
            log.info(`[GroupService] On-demand синхронизировано ${rawGroups.length} групп для programId ${programId}`);
            
            this.invalidateCache();
            await this._ensureCache().catch(() => {});
            return await this.getByProgramId(programId);
        } catch (e) {
            log.error(`[GroupService] Ошибка syncProgramGroups(${programId}): ` + e.message);
            return await this.getByProgramId(programId);
        }
    }

    findByName = async (name) => {
        try {
            await this._ensureCache();

            if (this._cache && this._cache.length > 0) {
                const normQuery = normalizeCyrillic(name);
                const tokens = normQuery.split(' ').filter(Boolean);
                const compactQuery = normQuery.replace(/\s+/g, '');

                if (tokens.length === 0) return [...this._cache];

                const results = this._cache.filter(g => {
                    if (!g || !g.name) return false;
                    const normName = normalizeCyrillic(g.name);
                    const compName = normName.replace(/\s+/g, '');

                    // 1. Компактное начало ("ис" -> "ис211", "уис" -> "уис221")
                    if (compName.startsWith(compactQuery)) return true;

                    // 2. Все токены запроса начинаются на слово в названии группы
                    const normTokens = normName.split(' ').filter(Boolean);
                    if (tokens.length > 0 && tokens.every(qTok => normTokens.some(nTok => nTok === qTok || nTok.startsWith(qTok)))) {
                        return true;
                    }

                    // 3. Компактное вхождение от 2 символов
                    if (compactQuery.length >= 2 && compName.includes(compactQuery)) {
                        return true;
                    }

                    return false;
                });

                // Сортировка по релевантности: точные совпадения и начинающиеся с запроса идут первыми
                return results.sort((a, b) => {
                    const aNorm = normalizeCyrillic(a.name);
                    const bNorm = normalizeCyrillic(b.name);
                    const aComp = aNorm.replace(/\s+/g, '');
                    const bComp = bNorm.replace(/\s+/g, '');

                    if (aComp === compactQuery && bComp !== compactQuery) return -1;
                    if (bComp === compactQuery && aComp !== compactQuery) return 1;

                    const aStarts = aComp.startsWith(compactQuery);
                    const bStarts = bComp.startsWith(compactQuery);
                    if (aStarts && !bStarts) return -1;
                    if (!aStarts && bStarts) return 1;

                    return a.name.localeCompare(b.name, 'ru', { numeric: true });
                });
            }

            // Fallback в MongoDB
            const escaped = String(name || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const regExp = new RegExp(escaped, "i");
            const docs = await Group.find({name:{$regex:regExp}}).sort('name').lean();
            const seen = new Set();
            return docs.filter(g => {
                if (seen.has(g.id)) return false;
                seen.add(g.id);
                return true;
            });
        } catch (e) {
            throw new Error("Ошибка при поиске группы по названию: " + e.stack);
        }
    }

    deduplicateGroups = async () => {
        try {
            const dups = await Group.aggregate([
                { $group: { _id: '$id', count: { $sum: 1 }, ids: { $push: '$_id' } } },
                { $match: { count: { $gt: 1 } } }
            ]);

            let deletedCount = 0;
            for (const dup of dups) {
                const [keep, ...removeIds] = dup.ids;
                if (removeIds.length > 0) {
                    const res = await Group.deleteMany({ _id: { $in: removeIds } });
                    deletedCount += res.deletedCount || 0;
                }
            }
            if (deletedCount > 0) {
                this.invalidateCache();
                await this._ensureCache().catch(() => {});
            }
            return deletedCount;
        } catch (e) {
            throw new Error("Ошибка при дедупликации групп: " + e.stack);
        }
    }
}

export default new groupService()