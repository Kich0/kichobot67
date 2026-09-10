import mongoose from "mongoose";

const groupSchema = new mongoose.Schema({
    name: {type: String, required: true},
    id: {type: Number, required: true, unique: true, index: true},
    language: {type: String},
    href: {type: String},
    age: {type: Number},
    studentCount: {type: Number},
    program: {type: Number, ref: "Program", field: 'id', required: true, unique: false},
}, {timestamps: true});

export const Group = mongoose.model('Group', groupSchema);

