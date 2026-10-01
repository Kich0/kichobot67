import mongoose from "mongoose";

const teacherSchema = new mongoose.Schema({
    name: {type: String, required: true},
    id: {type: Number, required: true, unique: true, index: true},
    href: {type: String, required: true},
    department: {type: Number, ref: "Department", field: 'id', required: true},
    fullName: {type: String},
    firstName: {type: String},
    lastName: {type: String},
    patronymic: {type: String},
    jobTitle: {type: String},
    photoUrl: {type: String},
    isHead: {type: Boolean, default: false},
    departmentName: {type: String},
    departmentIdOnSite: {type: Number}
}, {timestamps: true});

export const Teacher = mongoose.model('Teacher', teacherSchema)

