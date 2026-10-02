import mongoose from "mongoose";

const groupSchema = new mongoose.Schema({
    time: String,
    group: String,
    subject: String,
    lessonType: String
});

const teacherScheduleDaySchema = new mongoose.Schema({
    day: String,
    groups: [groupSchema]
});

// ВНИМАНИЕ: TTL-индекс ОТСУТСТВУЕТ намеренно!
// Данная коллекция хранит последнее актуальное расписание преподавателя как несгораемый аварийный резерв.
const teacherScheduleSchema = new mongoose.Schema({
    teacherId: { type: Number, ref: "teacher", field: 'id', required: true, unique: true, index: true },
    data: [teacherScheduleDaySchema]
}, { timestamps: true });

export const TeacherSchedule = mongoose.model('TeacherSchedule', teacherScheduleSchema);

