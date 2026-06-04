import ApiError from "../../exceptions/apiError.js";
import GroupService from "../../services/GroupService.js";

const isGroupMember = async (req, res, next) => {
    if (req.method === "OPTIONS") {
        next()
    }

    try {
        // Check for Global Site Admin role.
        if (req.user.roles.includes("Admin")){
            return next()
        }

        const groupId = req.body.groupId
        const userId = req.user.userId

        const group = await GroupService.get_one(groupId)
        if (!group) {
            return next(ApiError.BadRequest("Такой группы нет, брат."))
        }
        if (!group.members.includes(userId)) {
            return next(ApiError.Forbidden("Вы не состоите в данной группе. Пашел нахер, казел!"))
        }
        req.group = group
        next()
    } catch (e) {
        next(e)
    }
};

export default isGroupMember