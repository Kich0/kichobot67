import {Faculty} from "../models/faculty.js"
import {Program} from "../models/program.js";


class FacultyService {
    getIdByGroup = async (group) => {
        try {
            const program = await Program.findOne({ id: group.program })
            return program.faculty;
        }catch (e) {
            throw new Error("Ошибка при получении факультета по группе: " + e.stack)
        }
    }

    getById = async (id) => {
        try {
            return await Faculty.findOne({id})
        } catch (e) {
            throw new Error("Ошибка при получении факультета по айди: " + e.stack)
        }
    }

    getAll = async () => {
        try {
            return await Faculty.find({}).sort('id')
        } catch (e) {
            throw new Error("Ошибка при получении всех факультетов: " + e.stack)
        }
    }

    updateAll = async (faculties) => {
        try {
            if (!faculties || faculties.length === 0) {
                return;
            }
            const seen = new Set();
            const uniqueFaculties = [];
            for (const f of faculties) {
                if (f && f.id && !seen.has(f.id)) {
                    seen.add(f.id);
                    uniqueFaculties.push(f);
                }
            }
            const ops = uniqueFaculties.map(f => ({
                updateOne: {
                    filter: { id: f.id },
                    update: { $set: f },
                    upsert: true
                }
            }));
            await Faculty.bulkWrite(ops);
        } catch (e) {
            throw new Error("Ошибка при обновлении всех факультетов: " + e.stack)
        }
    }
}

export default new FacultyService()


