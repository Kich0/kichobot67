import {Group} from "../models/group.js";
import log from "../logging/logging.js";

class groupService {
    getByProgramId = async (programId) => {
        try {
            const docs = await Group.find({program: programId}).sort('-id');
            // Дедупликация по id на случай любых повторений
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
            return await Group.findOne({id})
        } catch (e) {
            throw new Error("Ошибка при получении группы по айди: " + e.stack)
        }
    }

    getAll = async () => {
        try {
            return await Group.find({})
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
            return await this.getByProgramId(programId);
        } catch (e) {
            log.error(`[GroupService] Ошибка syncProgramGroups(${programId}): ` + e.message);
            return await this.getByProgramId(programId);
        }
    }

    findByName = async (name) => {
        try {
            const regExp = new RegExp(name, "i")
            const docs = await Group.find({name:{$regex:regExp}}).sort('name')
            // Дедупликация по id для поисковой выдачи
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
            return deletedCount;
        } catch (e) {
            throw new Error("Ошибка при дедупликации групп: " + e.stack);
        }
    }
}

export default new groupService()