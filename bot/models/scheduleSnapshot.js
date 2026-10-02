import mongoose from "mongoose";

const subjectSchema = new mongoose.Schema({
    time: String,
    subject: String
}, { _id: false });

const scheduleDaySchema = new mongoose.Schema({
    day: String,
    subjects: [subjectSchema]
}, { _id: false });

/**
 * Historical schedule snapshot with 5-day TTL index.
 * Automatically removed by MongoDB after 5 days (432,000 seconds).
 */
const scheduleSnapshotSchema = new mongoose.Schema({
    groupId: { type: Number, required: true, index: true },
    data: [scheduleDaySchema],
    createdAt: {
        type: Date,
        default: Date.now,
        expires: 5 * 24 * 60 * 60 // 5 days in seconds (432,000s)
    }
}, { timestamps: false });

export const ScheduleSnapshot = mongoose.model('ScheduleSnapshot', scheduleSnapshotSchema);
export default ScheduleSnapshot;
