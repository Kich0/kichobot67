import {Schema, model} from "mongoose";

const GroupSchema = new Schema({
    name: { type: String, required: true },
    url: { type: String, required: true },
    owner: { type: Schema.Types.ObjectId, ref: "User", field: "_id" },
    members: [{ type: Schema.Types.ObjectId, ref: "User", field: "_id" }],
    admins: [{ type: Schema.Types.ObjectId, ref: "User", field: "_id" }],
    join_requests: [{ type: Schema.Types.ObjectId, ref: "User", field: "_id" }],
}, {
    timestamps: true,
});

export default model("Group", GroupSchema)