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

// Расписание преподавателя хранится бессрочно как несгораемый аварийный резерв.
// Здесь лежит ОДИН актуальный «оригинал»; при изменении содержимого (хоть одной буквы)
// прежний оригинал переносится в коллекцию TeacherScheduleVersion (см. scheduleVersioning.js).
const teacherScheduleSchema = new mongoose.Schema({
    teacherId: { type: Number, ref: "teacher", field: 'id', required: true, unique: true, index: true },
    data: [teacherScheduleDaySchema],
    hash: { type: String },          // SHA-256 содержимого
    changedAt: { type: Date },       // когда содержимое последний раз реально менялось
    checkedAt: { type: Date }        // когда расписание последний раз сверялось с API
}, { timestamps: true });

export const TeacherSchedule = mongoose.model('TeacherSchedule', teacherScheduleSchema);

// История прежних версий расписаний преподавателей.
// Каждая версия автоматически удаляется через 2 месяца после архивации (TTL по archivedAt),
// а суммарный размер истории ограничен 50 МБ (см. scheduleVersioning.js).
const teacherScheduleVersionSchema = new mongoose.Schema({
    teacherId: { type: Number, required: true },
    hash: { type: String, required: true },
    data: [teacherScheduleDaySchema],
    validFrom: { type: Date },
    validTo: { type: Date },
    diffSummary: { type: String },              // Краткое описание отличий
    diffDetails: { type: Array, default: [] },  // Структурированный массив изменений
    archivedAt: { type: Date, default: Date.now, expires: 60 * 24 * 60 * 60 } // 2 месяца
}, { timestamps: true });
teacherScheduleVersionSchema.index({ teacherId: 1, validTo: -1 });

export const TeacherScheduleVersion = mongoose.model('TeacherScheduleVersion', teacherScheduleVersionSchema);
