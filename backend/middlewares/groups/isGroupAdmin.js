import ApiError from "../../exceptions/apiError.js";
import GroupService from "../../services/GroupService.js";

const isGroupAdmin = async (req, res, next) => {
    if (req.method === "OPTIONS") {
        next()
    }

    try {
        const groupId = req.body.groupId
        const userId = req.user.userId

        const group = await GroupService.get_one(groupId)
        if (!group) {
            return next(ApiError.BadRequest("Такой группы не существует."))
        }
        if (!group.admins.includes(userId)) {
            return next(ApiError.Forbidden("Данную опцию может выполнить только администратор группы."))
        }
        req.group = group
        next()
    } catch (e) {
        next(e)
    }
};

export default isGroupAdmin