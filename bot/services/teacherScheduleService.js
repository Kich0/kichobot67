import {TeacherSchedule} from "../models/teacherSchedule.js"

class teacherScheduleService {
    updateByTeacherId = async (teacherId, data) => {
        try {
            const id = Number(teacherId) || teacherId;
            return await TeacherSchedule.findOneAndUpdate(
                { teacherId: id }, { teacherId: id, data }, { upsert: true, returnDocument: "after" }
            );

        } catch (e) {
            throw new Error("Ошибка при обновлении расписания по teacherId: " + e.stack)
        }
    }

    getByTeacherId = async (teacherId) => {
        try {
            const id = Number(teacherId) || teacherId;
            return await TeacherSchedule.findOne({ teacherId: id }).lean();
        } catch (e) {
            throw new Error("Ошибка при получении расписания по teacherId: " + e.stack)
        }
    }
}


export default new teacherScheduleService()