import log from "../logging/logging.js"
import facultyService from "../services/facultyService.js"
import programService from "../services/programService.js"
import groupService from "../services/groupService.js";
import axios from "axios";
import scheduleService from "../services/scheduleService.js";
import userService from "../services/userService.js";
import { unexpectedCallbackErrorController } from "../exceptions/bot/unexpectedCallbackErrorController.js";
import { bot } from "../app.js";
import { sleep } from "../handlers/adminCommandHandler.js";
import i18next from "i18next";
import {getAndSendUserInfoByUserId} from "./commands/adminCommands/getUser.js";
import config from "../config.js";

export let schedule_cache = {}

async function downloadSchedule(groupId, language, attemption = 1) {
    try {
        return await axios.get(`${config.KSU_HELPER_URL}/express/api/schedule/get_schedule_by_groupId/${groupId}/${language}`, {
        timeout: 10000
    })
    } catch (e) {
        if (attemption < 1) {
            await sleep(1000)
            log.info(`group ${groupId} попала в рекурсивную функцию по получению расписания!`)
            return await downloadSchedule(groupId, language, ++attemption)
        } else {
            throw e
        }
    }
}

class ScheduleController {
    getGroupsRowMarkup(data) {
        const day = this.getCurrentDayNumber()

        return {
            inline_keyboard: data.map((item) => [{
                text: item.name, callback_data: `chooseScheduleLanguage|${item.language}|${item.id}|${day}`
            }])
        }
    }

    getRowMarkup(data, refTo) {
        return {
            inline_keyboard: data.map((item) => [{
                text: item.name, callback_data: `${refTo}|${item.id}|0`
            }])
        }
    }

    configureMenuData(data, page, user_language) {
        const row_per_page = 10
        const page_count = Math.floor(data.length / row_per_page)
        if (page > page_count) {
            page = 0
        }
        if (page < 0) {
            page = page_count
        }
        const start_index = row_per_page * page;

        const currentPageText = `${i18next.t('page_text', { lng: user_language, page: page + 1, pageCount: page_count + 1 })}`

        return {
            data: data.slice(start_index, start_index + row_per_page), page, page_count, currentPageText
        }
    }

    formatElapsedTime(timestamp, user_language) {
        const now = new Date();
        const diffInSeconds = Math.floor((now - timestamp) / 1000);
        const diffInHours = Math.floor(diffInSeconds / 3600);

        let statusEmoji = '🟢';
        if (diffInHours >= 24) {
            statusEmoji = '🔴';
        } else if (diffInHours >= 5) {
            statusEmoji = '🟡';
        }

        if (diffInSeconds < 60) {
            return `${statusEmoji} ${diffInSeconds} ${i18next.t('second', {lng:user_language})}`;
        } else if (diffInSeconds < 3600) {
            const minutes = Math.floor(diffInSeconds / 60);
            return `${statusEmoji} ${minutes} ${i18next.t('minute', {lng:user_language})}`;
        } else if (diffInSeconds < 86400) {
            return `${statusEmoji} ${diffInHours} ${i18next.t('hour', {lng:user_language})}`;
        } else {
            const days = Math.floor(diffInSeconds / 86400);
            return `${statusEmoji} ${days} ${i18next.t('day', {lng:user_language})}`;
        }
    }

    formatTimestamp(timestamp) {
        const date = new Date(timestamp);

        const hours = String(date.getHours()).padStart(2, '0');
        const minutes = String(date.getMinutes()).padStart(2, '0');
        const seconds = String(date.getSeconds()).padStart(2, '0');

        return `${hours}:${minutes}:${seconds}`;
    }

    getCurrentDayNumber() {
        const currentDate = new Date();
        if (currentDate.getHours() >= 18) {
            return ((currentDate.getDay() + 6) % 7) + 1;
        }
        return (currentDate.getDay() + 6) % 7;
    }

    addGoBackBtnToMarkup(markup, refTo, lng) {
        markup.inline_keyboard.push([{
            text: `${i18next.t('go_prev_menu', { lng })}`, callback_data: refTo
        }])

        return markup
    }

    addPaginationBtnsToMarkup(markup, pageCount, page, refTo, lng) {
        if (pageCount > 0) {
            markup.inline_keyboard.push([{
                text: `◀️ ${i18next.t('go_back', { lng })}`,
                callback_data: `${refTo}|${page - 1}`
            },
            { text: `${i18next.t('page_mini_text', { lng, page: page + 1, pageCount: pageCount + 1 })}`, callback_data: `nothing` },
            { text: `${i18next.t('go_forward', { lng })} ▶️`, callback_data: `${refTo}|${page + 1}` }])
        }
        return markup
    }

    async getFacultyMenu(msgToEdit, prePage) {
        try {
            const user_language = await userService.getUserLanguage(msgToEdit.chat.id)

            const faculties = await facultyService.getAll()

            let { data, page, page_count, currentPageText } = this.configureMenuData(faculties, prePage, user_language)

            let markup = this.getRowMarkup(data, 'program')
            markup = this.addPaginationBtnsToMarkup(markup, page_count, page, 'faculty', user_language)
            markup = this.addGoBackBtnToMarkup(markup, 'start', user_language)

            const msgText = `${i18next.t('faculty_pick', { lng: user_language })} \n${currentPageText}`

            await bot.editMessageText(msgText, {
                chat_id: msgToEdit.chat.id, message_id: msgToEdit.message_id, reply_markup: markup
            })
        } catch (e) {
            throw e
        }

    }

    async getProgramMenu(msgToEdit, facultyId, prePage) {
        try {
            const user_language = await userService.getUserLanguage(msgToEdit.chat.id)

            const programs = await programService.getByFacultyId(facultyId)
            const faculty = await facultyService.getById(facultyId)

            const { data, page, page_count, currentPageText } = this.configureMenuData(programs, prePage, user_language)

            let markup = this.getRowMarkup(data, `group|${facultyId}`)

            markup = this.addPaginationBtnsToMarkup(markup, page_count, page, `program|${facultyId}`, user_language)
            markup = this.addGoBackBtnToMarkup(markup, 'faculty|0', user_language)

            const msgText = `${i18next.t('program_pick', { lng: user_language })}\n${i18next.t('faculty', { lng: user_language, faculty: faculty.name })}\n${currentPageText}`

            await bot.editMessageText(msgText, {
                chat_id: msgToEdit.chat.id, message_id: msgToEdit.message_id, reply_markup: markup
            })
        } catch (e) {
            throw e
        }
    }

    async getGroupMenu(msgToEdit, programId, facultyId, prePage) {
        try {
            const user_language = await userService.getUserLanguage(msgToEdit.chat.id)

            const groups = await groupService.getByProgramId(programId)
            const program = await programService.getById(programId)

            const { data, page, page_count, currentPageText } = this.configureMenuData(groups, prePage, user_language)

            let markup = this.getGroupsRowMarkup(data)

            markup = this.addPaginationBtnsToMarkup(markup, page_count, page, `group|${facultyId}|${programId}`, user_language)
            markup = this.addGoBackBtnToMarkup(markup, `program|${facultyId}|0`, user_language)

            const msgText = `${i18next.t('group_pick', { lng: user_language })}\n${i18next.t('program', { lng: user_language, program: program.name })}\n${currentPageText}`

            await bot.editMessageText(msgText, {
                chat_id: msgToEdit.chat.id, message_id: msgToEdit.message_id, reply_markup: markup
            })
        } catch (e) {
            throw e
        }
    }

    async sendSchedule(call, schedule_cache, preMessage = '') {
        try {
            const user_language = await userService.getUserLanguage(call.message.chat.id)

            const timestamp = schedule_cache.timestamp
            const data = schedule_cache.data
            const group = schedule_cache.group

            const data_array = call.data.split('|');
            let [, , , dayNumber] = data_array
            if (+dayNumber > 5) {
                dayNumber = 0
            }
            if (+dayNumber < 0) {
                dayNumber = 5
            }

            const scheduleLifeTime = this.formatElapsedTime(timestamp, user_language)
            const scheduleDateTime = this.formatTimestamp(timestamp)

            const schedule_day = data[dayNumber]['day']
            const preSchedule = data[dayNumber]['subjects'];

            const schedule = preSchedule.filter(obj => obj.subject !== '')

            let schedule_text = ``
            const headerText = `${i18next.t('group_and_year', { lng: user_language, groupName: group.name, groupYear: group.age })}\n📆 ${i18next.t('schedule_by_day', { lng: user_language, dayName: schedule_day })}\n`

            if (!schedule.length) {
                schedule_text = `<b>${i18next.t('vacation', { lng: user_language })}</b>\n`
            }
            for (const item of schedule) {
                schedule_text += '⌚️ ' + item.time + '\n'
                schedule_text += '📚 ' + item.subject + '\n'
            }
            let end_text = `🕒 <i><b>${scheduleLifeTime} || ${scheduleDateTime}</b></i>\n` +
                `${i18next.t('for_help', {lng:user_language})}\n`

            let msg_text = preMessage + headerText + schedule_text + end_text

            const preCallback = data_array.slice(0, -1).join("|")
            const facultyId = await facultyService.getIdByGroup(group) || 0

            let markup = {
                inline_keyboard: [
                    [{ text: `◀️ ${i18next.t('go_back', { lng: user_language })}`, callback_data: preCallback + `|${+dayNumber - 1}` }, {
                        text: `🔄`,
                        callback_data: 'refresh' + call.data
                    }, {
                        text: `${i18next.t('go_forward', { lng: user_language })} ▶️`, callback_data: preCallback + `|${+dayNumber + 1}`
                    }],
                    [{ text: `🔙 ${i18next.t('go_prev_menu', { lng: user_language })}`, callback_data: `group|${facultyId}|${group.program}|0` }]
                ]
            }
            await bot.editMessageText(msg_text,
                {
                    message_id: call.message.message_id,
                    chat_id: call.message.chat.id,
                    parse_mode: "HTML",
                    reply_markup: markup,
                    disable_web_page_preview: true
                })
        } catch (e) {
            await unexpectedCallbackErrorController(e, call.message, call.data)
        }
    }

    async getReservedSchedule(call, groupId, error_text) {
        const user_language = await userService.getUserLanguage(call.message.chat.id)
        const answer_msg_text = i18next.t('finding_reserved_schedule', {lng:user_language})
        await bot.editMessageText(answer_msg_text, {
            chat_id: call.message.chat.id, message_id: call.message.message_id
        })
        const response = await scheduleService.getByGroupId(groupId)
        if (response) {
            const updatedAt = new Date(response.updatedAt);
            const timestamp = updatedAt.getTime();

            const group = await groupService.getById(groupId)
            schedule_cache[groupId] = { data: response.data, timestamp, group }
            await this.sendSchedule(call, schedule_cache[groupId], `<b>${error_text} \n` +
                `${i18next.t('reserved_schedule_header', {lng:user_language})}\n\n</b>`)
        } else {
            const msg_text = i18next.t('reserved_schedule_not_found', {lng:user_language})
            await bot.editMessageText(msg_text, {
                chat_id: call.message.chat.id, message_id: call.message.message_id, reply_markup: {
                    inline_keyboard: [[{ text: i18next.t('try_again', {lng:user_language}), callback_data: call.data }]]
                }
            })
        }
    }

    async getScheduleMenu(call) {
        try {
            const data_array = call.data.split('|');
            let [, language, groupId] = data_array
            const groupIdent = `${groupId}|${language}`
            if (groupIdent in schedule_cache && Date.now() - schedule_cache[groupIdent].timestamp <= 30 * 60 * 1000) {
                await this.sendSchedule(call, schedule_cache[groupIdent])
            } else {
                await downloadSchedule(groupId, language)
                    .then(async (response) => {
                        const group = await groupService.getById(groupId)
                        schedule_cache[groupIdent] = { data: response.data, timestamp: Date.now(), group }
                        await this.sendSchedule(call, schedule_cache[groupIdent])

                        await scheduleService.updateByGroupId(groupId, response.data).catch(e => log.error(`Ошибка при попытке сохранить резервную копию расписания в бд. groupId:${groupId}. Пользователь никак не пострадал.`, {
                            stack: e.stack, call, userId: call.message.chat.id
                        }))
                    })
                    .catch(async (e) => {
                        try {
                            const user_language = await userService.getUserLanguage(call.message.chat.id)

                            let error_text = i18next.t('schedule_error', {lng:user_language})
                            if (e.response) {
                                if (e.response.status === 503)
                                    error_text = i18next.t('schedule_error_503', {lng:user_language})
                                if (e.response.status === 500) {
                                    error_text = i18next.t('schedule_error_500', {lng:user_language})
                                }
                            }
                            log.warn(`Student ${call.message.chat.id} from group ${groupId} gets a cached schedule.` + error_text + e.message, {
                                stack: e.stack,
                            })
                            await this.getReservedSchedule(call, groupId, error_text)
                        } catch (e) {
                            log.error("Ошбика при получении резервного расписания.", {
                                stack: e.stack,
                                call,
                                userId: call.message.chat.id
                            })
                            return await unexpectedCallbackErrorController(e, call.message, call.data)
                        }
                    })
            }
            const userOldData = await userService.updateUser(call.message.chat.id, {
                userId: call.message.chat.id,
                userType: String(call.message.chat.type),
                userTitle: call.message.chat.title,
                firstName: call.message.chat.first_name,
                lastName: call.message.chat.last_name,
                username: call.message.chat.username,
                group: groupId,
                scheduleType: "student"
            }).catch((e) => log.error("Ошибка при обновлении данных о пользователе при получении расписания. ", {
                stack: e.stack, call, userId: call.message.chat.id
            }))
            if (!userOldData.group){
                log.warn(`User ${call.message.chat.id} получил своё первое расписание. Юхууу! Щас вышлю инфу о нем. `)
                try {
                    await getAndSendUserInfoByUserId(call.message.chat.id, config.LOG_CHANEL_ID)
                } catch (notificationError) {
                    log.error(`Не удалось отправить уведомление о новом пользователе: ${notificationError.message}. Убедитесь, что LOG_CHANEL_ID в .env верный и бот добавлен в этот канал.`);
                }
            }
        } catch (e) {
            return await unexpectedCallbackErrorController(e, call.message, call.data)
        }

    }
    async chooseScheduleLanguage(call){
        const languages = {
            ru: "русский",
            рус: "русский",
            каз: "казахский",
            kz: "казахский",
        }
        try{
            const user_language = await userService.getUserLanguage(call.message.chat.id)
            const [,schedule_language, groupId, page] = call.data.split("|")

            if (languages[schedule_language] === languages[user_language]){
                call.data = call.data.replace('chooseScheduleLanguage', 'schedule')
                await this.getScheduleMenu(call)
            }else{
                const user_language = await userService.getUserLanguage(call.message.chat.id)
                const msg_text = i18next.t('schedule_language_pick', {lng:user_language, user_language:languages[user_language], schedule_language: languages[schedule_language]})
                const markup = {inline_keyboard:[
                    [
                        {text:'Қазақ', callback_data:['schedule', 'каз', groupId, page].join('|')}
                    ],
                    [
                        {text:"Русский", callback_data:['schedule', 'рус', groupId, page].join('|')}
                    ]
                ]}
                await bot.editMessageText(msg_text, {
                    chat_id: call.message.chat.id, message_id: call.message.message_id,
                    reply_markup:markup, parse_mode:'HTML'
                })
            }

        }catch (e){
            return await unexpectedCallbackErrorController(e, call.message, call.data)
        }
    }
}

export default new ScheduleController()
