import mongoose from "mongoose";

const blackListSchema = new mongoose.Schema(
    {
        userId: { type: Number, required: true, unique: true },
        type: { type: String, enum: ['ban', 'mute'], default: 'ban' },
        until: { type: Date, default: null },
        reason: { type: String, default: null },
        username: { type: String, default: null },
        bannedBy: { type: Number, default: null },
    },
    {
        timestamps: true, // Указываем использовать timestamps
    }
);
export const BlackList = mongoose.model('Black-List', blackListSchema)

