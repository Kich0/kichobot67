import {User} from "../models/user.js"
import log from "../logging/logging.js";

export const userLanguagesCache = {}

class UserService {
    constructor() {
        this.registeredUserIds = new Set();
    }

    async registerUser(msgOrCall, extra = {}) {
        try {
            if (!msgOrCall) return;
            const from = msgOrCall.from || (msgOrCall.chat ? msgOrCall.from : msgOrCall);
            const chat = msgOrCall.chat || msgOrCall.message?.chat || {};
            const rawId = from?.id || chat?.id || msgOrCall.userId || msgOrCall.id;
            const userId = Number(rawId);
            if (!userId || isNaN(userId)) return;

            if (this.registeredUserIds && this.registeredUserIds.has(userId)) {
                return;
            }

            const existingUser = await this.findUserById(userId);
            if (existingUser) {
                if (!this.registeredUserIds) this.registeredUserIds = new Set();
                this.registeredUserIds.add(userId);
                return existingUser;
            }

            const cleanUsername = (from?.username || chat?.username || extra.username || '')
                ? String(from?.username || chat?.username || extra.username).replace(/^@/, '')
                : null;

            const userData = {
                userId,
                userType: String(chat?.type || extra.userType || 'private'),
                userTitle: chat?.title || extra.userTitle || null,
                firstName: from?.first_name || chat?.first_name || extra.firstName || null,
                lastName: from?.last_name || chat?.last_name || extra.lastName || null,
                username: cleanUsername,
            };

            const newUser = await this.createUser(userData);
            if (!this.registeredUserIds) this.registeredUserIds = new Set();
            this.registeredUserIds.add(userId);

            log.warn(`Зарегистрирован новый пользователь! /get_user${userId}`, {
                user: newUser,
                userId
            });
            return newUser;
        } catch (e) {
            log.error("Ошибка при попытке зарегистрировать пользователя: " + e.message, {
                stack: e.stack,
                userId: msgOrCall?.chat?.id || msgOrCall?.from?.id
            });
        }
    }

    async createUser(userData) {
        try {
            return User.create(userData)
        } catch (error) {
            throw new Error('Ошибка при создании пользователя: ' + error.stack);
        }
    }

    async findUserById(userId) {
        try {
            return await User.findOne({userId});
        } catch (error) {
            throw new Error('Ошибка при поиске пользователя: ' + error.stack);
        }
    }

    async updateUser(userId, newData) {
        try {
            return await User.findOneAndUpdate({userId: userId}, newData, {upsert: true, returnDocument:"before"});
        } catch (error) {
            throw new Error('Ошибка при обновлении данных пользователя: ' + error.stack);
        }
    }

    async getUserById(userId) {
        try {
            return await User.findOne({userId});
        } catch (error) {
            throw new Error('Ошибка при поиске пользователя: ' + error.stack);
        }
    }

    async getUserByUsername(username) {
        try {
            if (!username) return null;
            const clean = String(username).replace(/^@/, '').trim();
            const regExp = new RegExp(`^${clean.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'i');
            return await User.findOne({ username: { $regex: regExp } });
        } catch (error) {
            throw new Error('Ошибка при поиске пользователя по нику: ' + error.stack);
        }
    }

    async findUser(identifier) {
        try {
            if (!identifier) return null;
            const str = String(identifier).trim();
            // Если чисто число — ищем по Telegram ID
            if (/^\d+$/.test(str)) {
                const user = await this.getUserById(Number(str));
                if (user) return user;
            }
            // Иначе ищем по username
            return await this.getUserByUsername(str);
        } catch (error) {
            throw new Error('Ошибка в findUser: ' + error.stack);
        }
    }

    async countDocuments() {
        try {
            return await User.countDocuments({})
        } catch (e) {
            throw new Error("Ошибка при получении кол-ва документов юзеров: " + e.stack)
        }
    }

    async isAdmin(userId) {
        try {
            const user = await User.findOne({userId})

            return user ? Boolean(user.isAdmin) : false
        } catch (e) {
            throw new Error("Ошибка при проверке юзера на админа: " + userId + e.stack)
        }
    }

    async getUsersCountByGroupId(groupId) {
        try {
            return await User.countDocuments({group: groupId})
        } catch (e) {
            throw new Error("Ошибка при получении количества пользователей по полю групАйди: " + e.stack)
        }
    }

    async getUsersByGroupId(groupId) {
        try {
            return await User.find({group: groupId})
        } catch (e) {
            throw new Error("Ошибка при получении пользователей по полю групАйди: " + e.stack)
        }
    }

    async getAll() {
        try {
            return await User.find({})
        } catch (e) {
            throw new Error("Ошибка при получении всех пользователей: " + e.stack)
        }
    }

    async getUserLanguage(userId) {
        try {
            if (userId in userLanguagesCache){
                return userLanguagesCache[userId]
            }
            const user = await User.findOne({userId})
            const language = user?.language ?? 'ru';
            userLanguagesCache[userId] = language
            return language ?? "ru"
        } catch (e) {
            throw new Error("Ошибка при получении языка пользователя. " + e.stack)
        }
    }

    async setUserLanguage(userId, language) {
        try {
            userLanguagesCache[userId] = language
            return await User.updateOne({userId}, { $set: { language:language } }, {new: true})
        } catch (e) {
            throw new Error("Ошибка при set языка пользователя. " + e.stack)
        }
    }
}


export default new UserService()
