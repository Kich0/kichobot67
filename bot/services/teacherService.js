import {Teacher} from "../models/teacher.js";


class teacherService {
    getById = async (id) => {
        try {
            return await Teacher.findOne({id})
        } catch (e) {
            throw new Error("Ошибка при получении Teacher по айди: " + e.stack)
        }
    }

    getByDepartmentId = async (departmentId) => {
        try {
            const docs = await Teacher.find({department: departmentId}).sort('name');
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
            const docs = await Teacher.find({}).sort('name');
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

            return await Teacher.bulkWrite(operations, { ordered: false });
        } catch (e) {
            throw new Error("Ошибка при обновлении всех Teacher: " + e.stack);
        }
    }

    findByName = async (name) => {
        try {
            const regExp = new RegExp(name, "i");
            const docs = await Teacher.find({name: {$regex: regExp}}).sort('name');
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
                // Сохраняем первый документ, удаляем остальные
                const [keep, ...removeIds] = dup.ids;
                if (removeIds.length > 0) {
                    const res = await Teacher.deleteMany({ _id: { $in: removeIds } });
                    deletedCount += res.deletedCount || 0;
                }
            }
            return deletedCount;
        } catch (e) {
            throw new Error("Ошибка при дедупликации учителей: " + e.stack);
        }
    }
}

export default new teacherService()