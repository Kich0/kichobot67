import ApiError from "../exceptions/apiError.js";
import {validationResult} from "express-validator";
import GroupService from "../services/GroupService.js";
import mongoose from "mongoose";
import {GroupDTO} from "../dtos/GroupDTO.js";


class GroupController {
    async create(req, res, next) {
        try {
            const errors = validationResult(req)
            if (!errors.isEmpty()) {
                return next(ApiError.BadRequest("Ошибка при валидации данных.", errors.array()))
            }

            const owner = req.user.userId
            const group_name = req.body.name

            const group = await GroupService.create(owner, group_name)
            return res.json(group)
        } catch (e) {
            console.log(e)
            next(e)
        }
    }

    // async get_all(req, res, next) {
    //     try {
    //         // Есть смысл пагинацию сделать. Фильтры, Ордеринг
    //         const groups = await GroupService.get_all()
    //         return res.json(groups)
    //     } catch (e) {
    //         console.log(e)
    //         next(e)
    //     }
    // }

    async get_one(req, res, next) {
        try {
            const groupId = req.params.group_id
            const userId = req.user.userId
            if (!mongoose.Types.ObjectId.isValid(groupId)) {
                return next(ApiError.BadRequest("Указан невалидный group_id"))
            }
            const group = await GroupService.get_one(groupId)
            if (!group) {
                return next(ApiError.Not_Found("Группа не найдена."))
            }
            if (!group.members.some(user => String(user._id) === userId)) {
                return next(ApiError.Forbidden("Вы не состоите в данной группе"))
            }
            const groupDTO = new GroupDTO(group)

            return res.json(groupDTO)

        } catch (e) {
            console.log(e)
            next(e)
        }
    }

    async get_one_mini(req, res, next) {
        try {
            const groupId = req.params.group_id
            if (!mongoose.Types.ObjectId.isValid(groupId)) {
                return next(ApiError.BadRequest("Указан невалидный group_id"))
            }
            const group = await GroupService.get_one(groupId)
            if (!group) {
                return next(ApiError.Not_Found("Группа не найдена."))
            }
            const groupDTO = new GroupDTO(group)

            return res.json(groupDTO.forRandom())
        } catch (e) {
            console.log(e)
            next(e)
        }
    }

    async delete(req, res, next) {
        try {
            const groupId = req.params.group_id
            const userId = req.user.userId
            const group = await GroupService.get_one(groupId)
            if (!group) {
                return next(ApiError.Not_Found())
            }
            if (String(group.owner._id) !== userId) {
                return next(ApiError.Forbidden())
            }
            await GroupService.delete(groupId)
            const groupDto = new GroupDTO(group)
            return res.json({message: "Группа успешно удалена", deletedGroup:groupDto})
        } catch (e) {
            next(e)
        }
    }

    async send_join_request(req, res, next) {
        try {
            const groupId = req.params.group_id
            const userId = req.user.userId

            const group = await GroupService.get_one(groupId)
            if (!group) {
                return next(ApiError.Not_Found())
            }
            if (group.members.some(user => String(user._id) === userId)){
                return next(ApiError.Conflict("Вы уже состоите в данной группе. "))
            }
            await GroupService.send_join_request(group, userId)
            const groupDto = new GroupDTO(group)
            return res.json({message: "Отправлен запрос на встулпение в группу. Ожидайте одобрения вашей заявки от администрации группы. ", joiningGroup:groupDto})
        } catch (e) {
            next(e)
        }
    }

    async accept_join_request(req, res, next) {
        try {
            const errors = validationResult(req)
            if (!errors.isEmpty()) {
                return next(ApiError.BadRequest("Ошибка при валидации данных.", errors.array()))
            }

            const groupId = req.params.group_id
            const userId = req.user.userId
            const requestAuthorId = req.body.requestAuthorId

            const group = await GroupService.get_one(groupId)
            if (!group) {
                return next(ApiError.Not_Found())
            }
            if (!group.admins.some(admin => String(admin._id) === userId)){
                return next(ApiError.Forbidden())
            }
            if (!group.join_requests.some(user => user._id.toString() === requestAuthorId)){
                return next(ApiError.Conflict("Пользователь не подавал заявку на вступление."))
            }
            const updatedGroup = await GroupService.accept_join_request(group, requestAuthorId)
            const updatedGroupDto = new GroupDTO(updatedGroup)
            return res.json({message:"Пользователь успешно добавлен в группу. ", group:updatedGroupDto, requestAuthorId})

        } catch (e) {
            next(e)
        }
    }



    // async give_group_admin_rights(req,res, next){
    //     try{
    //         const userId = req.body.userId
    //
    //         const group = req.group
    //         if (!group.members.includes(userId)){
    //             next(ApiError.Not_Found("Пользователь с указанным идентификатором не найден в группе."))
    //         }
    //         if (group.admins.includes(userId)){
    //             next(ApiError.Conflict("Пользователь уже является администратором группы."))
    //         }
    //
    //         res.json(GroupService.give_group_admin_rights(group.userId))
    //     }catch (e) {
    //         console.log(e)
    //         next(e)
    //     }
    // }
}

export default new GroupController()