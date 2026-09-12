import {Program} from "../models/program.js"

class programService{
    getByFacultyId = async (facultyId) => {
        try{
            return await Program.find({faculty: facultyId}).sort('name')
        }catch (e) {
            throw new Error("Ошибка при получении программы по факультиАйди: " + e.stack)
        }
    }

    getById = async (id) => {
        try{
            return await Program.findOne({id})
        }catch (e) {
            throw new Error("Ошибка при получении программы по айди: " + e.stack)
        }
    }

    updateAll = async (programs) => {
        try {
            if (!programs || programs.length === 0) {
                return;
            }
            const seen = new Set();
            const uniquePrograms = [];
            for (const p of programs) {
                if (p && p.id && !seen.has(p.id)) {
                    seen.add(p.id);
                    uniquePrograms.push(p);
                }
            }
            const ops = uniquePrograms.map(p => ({
                updateOne: {
                    filter: { id: p.id },
                    update: { $set: p },
                    upsert: true
                }
            }));
            await Program.bulkWrite(ops);
        } catch (e) {
            throw new Error("Ошибка при обновлении всех программ: " + e.stack)
        }
    }

    getAll = async () => {
        try{
            return await Program.find({})
        }catch (e) {
            throw new Error("Ошибка при получении всех программ: " + e.stack)
        }
    }


}

export default new programService()