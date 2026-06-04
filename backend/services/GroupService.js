import Group from "../models/Group.js";


class GroupService {
    async create(owner, name) {
        return Group.create({
            name,
            url: "Тут короче будет функция, которая будет генерировать рандомную залупу, и потом глядеть нет ли этой залупы уже у какой либо группы. если такая залупа есть то рекурсия. ГЕНИААААААААААААЛЬНО. КТо придумал? ВЛади придумал. ХЕХЕХЕХХЕХЕХХЕХЕХХЕ",
            owner,
            members: [owner],
            admins: [owner],
            join_requests: []
        })
    }

    async get_one(_id) {
        try {
            return Group.findById(_id).populate('owner members admins join_requests').exec();
        } catch (e) {
            throw new Error(`Ошибка при поптыке получить группу id: ${_id}. error: ${e.stack}`)
        }
    }

    async get_all() {
        try {
            return Group.find({});
        } catch (e) {
            throw new Error(`Ошибка при поптыке получить список всех групп. error: ${e.stack}`)
        }
    }

    async delete(_id) {
        try {
            return Group.deleteOne({_id}, {returnDocument: "before"});
        } catch (e) {
            throw new Error(`Ошибка при поптыке удалить группу id: ${_id}. error: ${e.stack}`)
        }
    }

    async get_all_by_userId(userId) {
        try {
            return Group.find({members: userId}).populate('owner members admins join_requests').exec();
        } catch (e) {
            throw new Error(`Ошибка при попытке получить список групп юзера: ${userId}. error: ${e.stack}`)
        }
    }

    async give_group_admin_rights(group, userId) {
        try {
            group.admins.push(userId);

            return group.save();
        } catch (e) {
            throw new Error(`Ошибка при попытке выдать права администратора пользователю: ${userId}. error: ${e.stack}`)
        }
    }

    async send_join_request(group, userId) {
        try {
            group.join_requests.push(userId);

            return group.save();
        } catch (e) {
            throw new Error(`Ошибка при попытке записать юзера в список подавших заявку на вступление: ${userId}. error: ${e.stack}`)
        }
    }


    async accept_join_request(group, userId) {
        try {
            if (!group.members.some(user => String(user._id) === userId)) {
                group.members.push(userId)
            }
            group.join_requests = group.join_requests.filter(someUser => someUser._id.toString() !== userId)
            await group.populate('members join_requests')
            return group.save();
        } catch (e) {
            throw new Error(`Ошибка при попытке записать юзера в список участников или убрать из списка подавших заявку: ${userId}. error: ${e.stack}`)
        }
    }
}

export default new GroupService()