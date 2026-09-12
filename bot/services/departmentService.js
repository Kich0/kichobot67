import {Department} from "../models/department.js";


class departmentService {
    getById = async (id) => {
        try {
            return await Department.findOne({id})
        } catch (e) {
            throw new Error("Ошибка при получении department по айди: " + e.stack)
        }
    }
    getAll = async () => {
        try {
            const docs = await Department.find({}).sort('name');
            const seen = new Set();
            return docs.filter(d => {
                if (seen.has(d.id)) return false;
                seen.add(d.id);
                return true;
            });
        } catch (e) {
            throw new Error("Ошибка при получении всех Department: " + e.stack)
        }
    }

    updateAll = async (departments) => {
        try {
            if (!departments || departments.length === 0) return null;

            // Дедупликация в памяти
            const seen = new Set();
            const uniqueDepartments = [];
            for (const d of departments) {
                if (d && d.id && !seen.has(d.id)) {
                    seen.add(d.id);
                    uniqueDepartments.push(d);
                }
            }

            // Атомарный upsert без deleteMany (zero-downtime, без дубликатов)
            const operations = uniqueDepartments.map(department => ({
                updateOne: {
                    filter: { id: department.id },
                    update: { $set: department },
                    upsert: true
                }
            }));

            return await Department.bulkWrite(operations, { ordered: false });
        } catch (e) {
            throw new Error("Ошибка при обновлении всех Department: " + e.stack)
        }
    }
}

export default new departmentService()


