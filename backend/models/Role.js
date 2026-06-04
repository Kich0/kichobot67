import {Schema, model} from "mongoose";

const RoleSchema = new Schema({
        value: { type: String, required: true, default: "User" },
    }, {
        timestamps: true,
    },
)

export default model("Role", RoleSchema)