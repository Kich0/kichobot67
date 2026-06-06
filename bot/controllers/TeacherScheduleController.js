import departmentService from "../services/departmentService.js";
import ScheduleController, {schedule_cache} from "./ScheduleController.js";
import teacherService from "../services/teacherService.js";
import axios from "axios";
import log from "../logging/logging.js";
import {unexpectedCallbackErrorController} from "../exceptions/bot/unexpectedCallbackErrorController.js";
import userService from "../services/userService.js";
import teacherScheduleService from "../services/teacherScheduleService.js";
import {bot} from "../app.js";
import i18next from 'i18next'
import config from "../config.js";

async function downloadSchedule(teacherId, attemption = 1) {
    try {
        return await axios.get(`${config.KSU_HELPER_URL}/express/api/teacherSchedule/get_teacher_schedule/${teacherId}`, {
            timeout: 10000
        })
    } catch (e) {
        if (attemption < 1) {
            log.info(`teacher ${teacherId} попал в рекурсивную функцию по получению расписания!`)
            return await downloadSchedule(teacherId, ++attemption)
        }else {
            throw e
        }
    }
}

class TeacherScheduleController {
    getTeachersRowMarkup(data){
        const day = ScheduleController.getCurrentDayNumber()

        return {
            inline_keyboard: data.map((item) => [{
                text: item.name, callback_data: `TeacherSchedule|${item.id}|${day}`
            }])
        }
    }

    transformGroupString(inputString) {
        const regex = /\((\d+)\/(\d+)\)/g;
        let resultString = inputString.replace(regex, '(Ауд. $1 | $2 корпус)');
        const parts = resultString.split(') ');
        if (parts.length > 1) {
            for (let i = 0; i < parts.length - 1; i++) {
                parts[i] += ')\n';
            }
            resultString = parts.join('');
        }
        resultString = resultString.trim();

        return resultString;
    }

    addSymbolToEachLine(inputString, symbol) {
        const lines = inputString.split('\n');
        const linesWithSymbol = lines.map((line) => `${symbol} ${line}`);
        return linesWithSymbol.join('\n');
    }

    async getDepartmentMenu(msgToEdit, prePage) {
        try {
            const user_language = await userService.getUserLanguage(msgToEdit.chat.id)

            const departments = await departmentService.getAll()

            const {data, page, page_count, currentPageText} = ScheduleController.configureMenuData(departments, prePage, user_language)

            let markup = ScheduleController.getRowMarkup(data, 'teacher')
            markup = ScheduleController.addPaginationBtnsToMarkup(markup, page_count, page, 'department', user_language)
            markup = ScheduleController.addGoBackBtnToMarkup(markup, 'start', user_language)

            const currentMenuText = `📌 ${i18next.t('department_pick', { lng: user_language })}`
            const menuHint = i18next.t('department_pick_hint', {lng:user_language})

            const msgText = `${currentMenuText}\n${menuHint}\n${currentPageText}`

            await bot.editMessageText(msgText, {
                chat_id: msgToEdit.chat.id, message_id: msgToEdit.message_id, reply_markup: markup
            })

        } catch (e) {
            throw e
        }
    }

    async getTeacherMenu(msgToEdit, departmentId, prePage) {
        try {
            const user_language = await userService.getUserLanguage(msgToEdit.chat.id)

            const teachers = await teacherService.getByDepartmentId(departmentId)
            const department = await departmentService.getById(departmentId)

            const {data, page, page_count, currentPageText} = ScheduleController.configureMenuData(teachers, prePage, user_language)

            let markup = this.getTeachersRowMarkup(data)
            markup = ScheduleController.addPaginationBtnsToMarkup(markup, page_count, page, `teacher|${departmentId}`, user_language)
            markup = ScheduleController.addGoBackBtnToMarkup(markup, 'department|0', user_language)

            const currentMenuText = `📌 ${i18next.t('teacher_pick', { lng: user_language })}\n📘 ${i18next.t('department', { lng: user_language, departmentName: department.name })}`
            const msgText = `${currentMenuText}\n${currentPageText}`

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
            const teacher = schedule_cache.teacher

            const data_array = call.data.split('|');
            let [, , dayNumber] = data_array
            if (+dayNumber > 5) {
                dayNumber = 0
            }
            if (+dayNumber < 0) {
                dayNumber = 5
            }

            const scheduleLifeTime = ScheduleController.formatElapsedTime(timestamp, user_language)
            const scheduleDateTime = ScheduleController.formatTimestamp(timestamp)

            const schedule_day = data[dayNumber]['day']
            const preSchedule = data[dayNumber]['groups']

            const schedule = preSchedule.filter(obj => obj.group !== '')

            let schedule_text = ``
            const headerText = `👥 <u>${teacher.name}</u>\n📆 ${i18next.t('schedule_by_day', { lng: user_language, dayName: schedule_day })}\n`

            if (!schedule.length) {
                schedule_text = `🥳 <b>${i18next.t('vacation', { lng: user_language })}</b>\n`
            }
            for (const item of schedule) {
                schedule_text += '⌚️ ' + item.time + '\n'
                schedule_text += this.addSymbolToEachLine(this.transformGroupString(item.group), '📚') + '\n\n'
            }
            let end_text = `🕰 <i><b>${i18next.t('schedule_downloaded', {lng:user_language, timeAgo:scheduleLifeTime})} || ${scheduleDateTime}</b></i>\n` +
                `📖 ${i18next.t('for_help', {lng:user_language})}\n`

            let msg_text = preMessage + headerText + schedule_text + end_text

            const preCallback = data_array.slice(0, -1).join("|")
            const departmentId = teacher.department || 0;

            let markup = {
                inline_keyboard: [
                    [{ text: `⬅️${i18next.t('go_back', {lng:user_language})}`, callback_data: preCallback + `|${+dayNumber - 1}` }, {
                        text: `🔄`,
                        callback_data: 'refresh' + call.data
                    }, {
                        text: `${i18next.t('go_forward', {lng:user_language})}➡️`, callback_data: preCallback + `|${+dayNumber + 1}`
                    }],
                    [{ text: `🔙 ${i18next.t('go_prev_menu', { lng: user_language })}`, callback_data: `teacher|${departmentId}|0` }]
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

    async getScheduleMenu(call) {
        try {
            const data_array = call.data.split('|');
            let [, teacherId] = data_array

            if (teacherId in schedule_cache && Date.now() - schedule_cache[teacherId].timestamp <= 30 * 60 * 1000) {
                await this.sendSchedule(call, schedule_cache[teacherId])
            } else {
                await downloadSchedule(teacherId)
                    .then(async (response) => {
                        const teacher = await teacherService.getById(teacherId)
                        schedule_cache[teacherId] = {data: response.data, timestamp: Date.now(), teacher}
                        await this.sendSchedule(call, schedule_cache[teacherId])

                        await teacherScheduleService.updateByTeacherId(teacherId, response.data).catch(e => log.error(`Ошибка при попытке сохранить резервную копию teacher расписания в бд. teacherId:${teacherId}. Пользователь никак не пострадал.`, {
                            stack: e.stack, call, userId: call.message.chat.id
                        }))
                    })
                    .catch(async (e) => {
                        try {
                            const user_language = await userService.getUserLanguage(call.message.chat.id)

                            let error_text = "⚠️ Произошла непредвиденная ошибка. Не получилось загрузить ваше расписание. Попробуйте обновить расписание."
                            if (e.response) {
                                if (e.response.status === 503)
                                    error_text = i18next.t('schedule_error_503')

                                if (e.response.status === 500) {
                                    error_text = i18next.t('schedule_error_500')
                                }
                            }
                            log.warn(`Teacher ${call.message.chat.id} | ${teacherId} gets a cached schedule.` + error_text + e.message, {
                                stack: e.stack,
                            })
                            await this.getReservedSchedule(call, teacherId, error_text)
                        } catch (e) {
                            log.error("Ошбика при получении резервного Teacher расписания.", {
                                stack: e.stack,
                                call,
                                userId: call.message.chat.id
                            })
                            return await unexpectedCallbackErrorController(e, call.message, call.data)
                        }
                    })
            }
            await userService.updateUser(call.message.chat.id, {
                userId: call.message.chat.id,
                userType: String(call.message.chat.type),
                userTitle: call.message.chat.title,
                firstName: call.message.chat.first_name,
                lastName: call.message.chat.last_name,
                username: call.message.chat.username,
                teacher: teacherId,
                scheduleType: 'teacher'
            }).catch((e) => log.error("Ошибка при обновлении данных о пользователе при получении Teacher расписания. ", {
                stack: e.stack, call, userId: call.message.chat.id
            }))

        } catch (e) {
            return await unexpectedCallbackErrorController(e, call.message, call.data)
        }

    }

    async getReservedSchedule(call, teacherId, error_text) {
        const user_language = await userService.getUserLanguage(call.message.chat.id)
        const answer_msg_text = i18next.t('finding_reserved_schedule', {lng:user_language})

        await bot.editMessageText(answer_msg_text, {
            chat_id: call.message.chat.id, message_id: call.message.message_id
        })
        const response = await teacherScheduleService.getByTeacherId(teacherId)
        if (response) {
            const updatedAt = new Date(response.updatedAt);
            const timestamp = updatedAt.getTime();

            const teacher = await teacherService.getById(teacherId)
            schedule_cache[teacherId] = {data: response.data, timestamp, teacher}
            await this.sendSchedule(call, schedule_cache[teacherId], `<b>${error_text} \n` +
                `${i18next.t('reserved_schedule_header', {lng:user_language})}\n\n</b>`)
        } else {
            const msg_text = i18next.t('reserved_schedule_not_found', {lng:user_language})
            await bot.editMessageText(msg_text, {
                chat_id: call.message.chat.id, message_id: call.message.message_id, reply_markup: {
                    inline_keyboard: [[{text: i18next.t('try_again', {lng:user_language}), callback_data: call.data}]]
                }
            })
        }
    }
}

export default new TeacherScheduleController()