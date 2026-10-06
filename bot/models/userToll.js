import mongoose from "mongoose";

const userTollSchema = new mongoose.Schema(
    {
        userId: { type: Number, required: true, unique: true },
        username: { type: String, default: null },
        enabled: { type: Boolean, default: true },
        starPrice: { type: Number, default: 9000 },
        paidCredits: { type: Number, default: 0 },
        addedBy: { type: Number, default: null },
    },
    {
        timestamps: true,
    }
);

export const UserToll = mongoose.model('UserToll', userTollSchema);
