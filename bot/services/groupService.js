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
            // Атомарный upsert по уникальному ID группы (zero-downtime)
            const operations = groups.map(group => ({
                updateOne: {
                    filter: { id: group.id },
                    update: { $set: group },
                    upsert: true
                }
            }));
            const res = await Group.bulkWrite(operations, { ordered: false });

            // Удаляем старые группы ТОЛЬКО при полном синке всего университета (>= 500 групп)
            if (removeStale || groups.length >= 500) {
                const activeIds = groups.map(g => g.id).filter(Boolean);
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
        }catch (e) {
            throw new Error("Ошибка при поиске группы по названию." + e.stack)
        }
    }
}

export default new groupService()