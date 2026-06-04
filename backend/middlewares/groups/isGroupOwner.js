import ApiError from "../../exceptions/apiError.js";
import GroupService from "../../services/GroupService.js";

const isGroupOwner = async (req, res, next) => {
    if (req.method === "OPTIONS") {
        next()
    }

    try {
        const groupId = req.params.group_id
        const userId = req.user.userId

        console.log(groupId)
        const group = await GroupService.get_one(groupId)
        if (!group) {
            return next(ApiError.BadRequest("Такой группы нет, брат."))
        }
        if (group.owner !== userId) {
            return next(ApiError.Forbidden("Данную опцию может сделать только владелец группы. "))
        }
        req.group = group
        console.log(group)
        next()
    } catch (e) {
        next(e)
    }
};

export default isGroupOwner