import mongoose from "mongoose";

const userActionSchema = new mongoose.Schema({
    userId: { type: Number, required: true, index: true },
    username: { type: String, index: true },
    action: { type: String, required: true },
    text: { type: String, required: true },
    entityId: { type: Number },
    entityName: { type: String },
    createdAt: { type: Date, default: Date.now, index: true }
}, {
    timestamps: true
});

export const UserAction = mongoose.model('UserAction', userActionSchema);
