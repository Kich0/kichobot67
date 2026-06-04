import mongoose from "mongoose";

const programSchema = new mongoose.Schema({
    name: {type: String},
    id: {type: Number},
    faculty: {type: Number, ref: "Faculty", field:'id', required: true, unique: false},
    facultyName: {type:String}
}, {timestamps:true});

export const Program = mongoose.model('Program', programSchema);
