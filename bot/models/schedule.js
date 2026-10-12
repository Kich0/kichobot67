import mongoose from "mongoose";

const subjectSchema = new mongoose.Schema({
    time: String,
    subject: String
});

const scheduleDaySchema = new mongoose.Schema({
    day: String,
    subjects: [subjectSchema]
});

// Расписание группы хранится бессрочно как несгораемый аварийный резерв.
// Здесь лежит ОДИН актуальный «оригинал»; при изменении содержимого (хоть одной буквы)
// прежний оригинал переносится в коллекцию ScheduleVersion (см. scheduleVersioning.js).
const scheduleSchema = new mongoose.Schema({
    groupId: { type: Number, unique: true },
    data: [scheduleDaySchema],
    hash: { type: String },          // SHA-256 содержимого
    changedAt: { type: Date },       // когда содержимое последний раз реально менялось
    checkedAt: { type: Date }        // когда расписание последний раз сверялось с API
}, { timestamps: true });

export const Schedule = mongoose.model('Schedule', scheduleSchema);

// История прежних версий расписаний групп (не удаляется, только страховочная обрезка по лимиту)
const scheduleVersionSchema = new mongoose.Schema({
    groupId: { type: Number, required: true },
    hash: { type: String, required: true },
    data: [scheduleDaySchema],
    validFrom: { type: Date },
    validTo: { type: Date }
}, { timestamps: true });
scheduleVersionSchema.index({ groupId: 1, validTo: -1 });

export const ScheduleVersion = mongoose.model('ScheduleVersion', scheduleVersionSchema);
