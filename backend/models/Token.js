import {Schema, model} from "mongoose";

const TokenSchema = new Schema({
        user: {type: Schema.Types.ObjectId, ref: "User", required: true},
        refreshToken: {type: String, required: true, default: "User"},
    }, {
        timestamps: true,
    },
)

export default model("Token", TokenSchema)