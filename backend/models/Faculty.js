import mongoose from "mongoose";

const facultySchema = new mongoose.Schema({
    name: {type: String},
    id: {type: Number},
}, {timestamps:true});

export const Faculty = mongoose.model('Faculty', facultySchema);
