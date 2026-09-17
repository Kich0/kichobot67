import log from "../logging/logging.js"
import facultyService from "../services/facultyService.js"
import programService from "../services/programService.js"
import groupService from "../services/groupService.js";
import scheduleService from "../services/scheduleService.js";
import userService from "../services/userService.js";
import { unexpectedCallbackErrorController } from "../exceptions/bot/unexpectedCallbackErrorController.js";
import { bot } from "../app.js";
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
import i18next from "i18next";
import {getAndSendUserInfoByUserId} from "./commands/adminCommands/getUser.js";
import config from "../config.js";
import userActionService from "../services/userActionService.js";
// ПРЯМОЙ ИМПОРТ бэкенд-сервиса вместо HTTP
import BackendScheduleService from "../../backend/services/ScheduleService.js";

export let schedule_cache = {}

async function downloadSchedule(groupId, language, attemption = 1) {
    try {
        // Прямой вызов вместо axios.get(KSU_HELPER_URL/...)
        // Возвращаем объект с .data для совместимости с остальным кодом
        const data = await BackendScheduleService.get_schedule_by_groupId(groupId, language);
        return { data, status: 200 };
    } catch (e) {
        if (e.code === 'NO_SCHEDULE') {
            throw e;
        }
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
            const seconds = diffInSeconds % 60;
            if (seconds > 0) {
                return `${statusEmoji} ${minutes} ${i18next.t('minute', {lng:user_language})} ${seconds} ${i18next.t('second', {lng:user_language})}`;
            }
            return `${statusEmoji} ${minutes} ${i18next.t('minute', {lng:user_language})}`;
        } else if (diffInSeconds < 86400) {
            return `${statusEmoji} ${diffInHours} ${i18next.t('hour', {lng:user_language})}`;
        } else {
            const days = Math.floor(diffInSeconds / 86400);
            return `${statusEmoji} ${days} ${i18next.t('day', {lng:user_language})}`;
        }
    }
    static KZ_OFFSET_MS = 5 * 60 * 60 * 1000;

    
    static getKZDate(date = new Date()) {
        return new Date(date.getTime() + ScheduleController.KZ_OFFSET_MS);
    }

    formatTimestamp(timestamp) {
        const kzDate = ScheduleController.getKZDate(new Date(timestamp));

        const hours = String(kzDate.getUTCHours()).padStart(2, '0');
        const minutes = String(kzDate.getUTCMinutes()).padStart(2, '0');
        const seconds = String(kzDate.getUTCSeconds()).padStart(2, '0');

        return `${hours}:${minutes}:${seconds}`;
    }

    getCurrentDayNumber() {
        const kzDate = ScheduleController.getKZDate();
        if (kzDate.getUTCHours() >= 18) {
            return ((kzDate.getUTCDay() + 6) % 7) + 1;
        }
        return (kzDate.getUTCDay() + 6) % 7;
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

            let groups = await groupService.getByProgramId(programId)
            // On-demand: если групп в базе нет, опрашиваем КарГУ в реальном времени
            if (!groups || groups.length === 0) {
                groups = await groupService.syncProgramGroups(programId);
            }

            const program = await programService.getById(programId)

            const { data, page, page_count, currentPageText } = this.configureMenuData(groups || [], prePage, user_language)

            let markup = this.getGroupsRowMarkup(data)

            markup = this.addPaginationBtnsToMarkup(markup, page_count, page, `group|${facultyId}|${programId}`, user_language)
            markup = this.addGoBackBtnToMarkup(markup, `program|${facultyId}|0`, user_language)

            const programName = program ? program.name : '';
            const msgText = `${i18next.t('group_pick', { lng: user_language })}\n${i18next.t('program', { lng: user_language, program: programName })}\n${currentPageText}`

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
            let group = schedule_cache.group

            const data_array = call.data.split('|');
            let [, , groupId, dayNumber] = data_array
            if (+dayNumber > 5) {
                dayNumber = 0
            }
            if (+dayNumber < 0) {
                dayNumber = 5
            }

            // Если group не было в объекте кэша — пробуем подгрузить из базы
            if (!group && groupId) {
                group = await groupService.getById(Number(groupId)).catch(() => null);
                if (group) {
                    schedule_cache.group = group;
                }
            }

            // Если во всей неделе нет ни одного предмета — показываем экран об отсутствии расписания
            const hasAnySubjects = Array.isArray(data) && data.some(day =>
                Array.isArray(day.subjects) && day.subjects.some(s => s && s.subject && s.subject.trim() !== '')
            );
            if (!hasAnySubjects) {
                return await this.handleNoSchedule(call, groupId, group?.name);
            }

            const scheduleLifeTime = this.formatElapsedTime(timestamp, user_language)
            const scheduleDateTime = this.formatTimestamp(timestamp)

            const schedule_day = data[dayNumber]['day']
            const preSchedule = data[dayNumber]['subjects'];

            const schedule = preSchedule.filter(obj => obj.subject !== '')

            let schedule_text = ``
            const groupName = group?.name || `Группа ${groupId || ''}`
            const groupYear = group?.age || ''
            const headerText = group
                ? `${i18next.t('group_and_year', { lng: user_language, groupName, groupYear })}\n📆 ${i18next.t('schedule_by_day', { lng: user_language, dayName: schedule_day })}\n`
                : `👥 <b>${groupName}</b>\n📆 ${i18next.t('schedule_by_day', { lng: user_language, dayName: schedule_day })}\n`

            if (!schedule.length) {
                schedule_text = `<b>${i18next.t('vacation', { lng: user_language })}</b>\n`
            } else {
                const formattedItems = schedule.map(item => {
                    return '⌚️ ' + item.time + '\n📚 ' + (item.subject || '').trim();
                });
                schedule_text = formattedItems.join('\n\n') + '\n';
            }
            let end_text = `🕒 <i><b>${scheduleLifeTime} || ${scheduleDateTime}</b></i>\n` +
                `${i18next.t('for_help', {lng:user_language})}\n` +
                `${i18next.t('official_site_compare', {lng:user_language})}\n`

            let msg_text = preMessage + headerText + schedule_text + end_text

            const preCallback = data_array.slice(0, -1).join("|")
            let facultyId = schedule_cache.facultyId;
            if (facultyId === undefined && group) {
                try {
                    facultyId = await facultyService.getIdByGroup(group) || 0;
                    schedule_cache.facultyId = facultyId;
                } catch (ignore) {}
            }
            facultyId = facultyId || 0;

            const backCallback = group?.program ? `group|${facultyId}|${group.program}|0` : 'start'

            let markup = {
                inline_keyboard: [
                    [{ text: `◀️ ${i18next.t('go_back', { lng: user_language })}`, callback_data: preCallback + `|${+dayNumber - 1}` }, {
                        text: `🔄`,
                        callback_data: 'refresh' + call.data
                    }, {
                        text: `${i18next.t('go_forward', { lng: user_language })} ▶️`, callback_data: preCallback + `|${+dayNumber + 1}`
                    }],
                    [{ text: `🔙 ${i18next.t('go_prev_menu', { lng: user_language })}`, callback_data: backCallback }]
                ]
            }
            await bot.editMessageText(msg_text,
                {
                    message_id: call.message.message_id,
                    chat_id: call.message.chat.id,
                    parse_mode: "HTML",
                    reply_markup: markup,
                    disable_web_page_preview: true
                }).catch(err => {
                    if (err.message && err.message.includes('message is not modified')) {
                        return;
                    }
                    throw err;
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
        const hasSubjects = Array.isArray(response?.data) && response.data.some(day =>
            Array.isArray(day.subjects) && day.subjects.some(s => s && s.subject && s.subject.trim() !== '')
        );
        if (response && hasSubjects) {
            const updatedAt = new Date(response.updatedAt);
            const timestamp = updatedAt.getTime();

            const group = await groupService.getById(Number(groupId)).catch(() => null)
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

    async handleScheduleError(e, call, groupId) {
        try {
            const user_language = await userService.getUserLanguage(call.message.chat.id);
            let error_text = i18next.t('schedule_error', { lng: user_language });
            if (e.response) {
                if (e.response.status === 503) error_text = i18next.t('schedule_error_503', { lng: user_language });
                if (e.response.status === 500) error_text = i18next.t('schedule_error_500', { lng: user_language });
            }
            log.warn(`Student ${call.message.chat.id} from group ${groupId} gets a cached schedule. ` + error_text + e.message, {
                stack: e.stack,
            });
            await this.getReservedSchedule(call, groupId, error_text);
        } catch (err) {
            log.error("Ошибка при получении резервного расписания.", {
                stack: err.stack,
                call,
                userId: call.message.chat.id
            });
            await unexpectedCallbackErrorController(err, call.message, call.data);
        }
    }

    async handleNoSchedule(call, groupId, groupName) {
        try {
            const user_language = await userService.getUserLanguage(call.message.chat.id);
            const group = await groupService.getById(Number(groupId)).catch(() => null);
            const finalGroupName = groupName || group?.name || `Группа ${groupId}`;

            let facultyId = 0;
            if (group) {
                try {
                    facultyId = await facultyService.getIdByGroup(group) || 0;
                } catch (ignore) {}
            }
            const backCallback = group?.program ? `group|${facultyId}|${group.program}|0` : 'start';

            const msg_text = i18next.t('no_schedule_for_group', { lng: user_language, groupName: finalGroupName });

            const markup = {
                inline_keyboard: [
                    [{ text: `🔄 ${i18next.t('try_again', { lng: user_language })}`, callback_data: call.data }],
                    [{ text: `🔙 ${i18next.t('go_prev_menu', { lng: user_language })}`, callback_data: backCallback }]
                ]
            };

            await bot.editMessageText(msg_text, {
                chat_id: call.message.chat.id,
                message_id: call.message.message_id,
                parse_mode: 'HTML',
                reply_markup: markup,
                disable_web_page_preview: true
            }).catch(err => {
                if (err.message && err.message.includes('message is not modified')) {
                    return;
                }
                throw err;
            });
        } catch (err) {
            log.error("Ошибка при отображении экрана отсутствия расписания:", {
                stack: err.stack,
                call,
                userId: call.message.chat.id
            });
            await unexpectedCallbackErrorController(err, call.message, call.data);
        }
    }

    async getScheduleMenu(call, forceRefresh = false) {
        try {
            const isRefresh = forceRefresh || call.data.includes("refresh");
            if (call.data.includes("refresh")) {
                call.data = call.data.replace('refresh', '');
            }
            const data_array = call.data.split('|');
            let [, language, groupId] = data_array;
            const groupIdent = `${groupId}|${language}`;
            const cached = schedule_cache[groupIdent];
            const now = Date.now();
            const REFRESH_COOLDOWN = 10 * 60 * 1000; // 10 мин кулдаун для кнопки 🔄
            const CACHE_MAX_AGE = 30 * 60 * 1000;    // 30 мин хранение в оперативной памяти

            if (isRefresh) {
                // Пользователь нажал кнопку «Обновить»
                bot.answerCallbackQuery(call.id).catch(() => {});
                if (cached && (now - cached.timestamp < REFRESH_COOLDOWN)) {
                    // Кулдаун 10 минут еще не прошел: просто тихо перерисовываем, чтобы обновить счётчик снизу (например, "1 мин. 30 сек.")
                    await this.sendSchedule(call, cached);
                } else {
                    // Прошло >= 10 минут (или кэша нет) — скачиваем свежее расписание
                    try {
                        const response = await downloadSchedule(groupId, language);
                        const group = cached?.group || await groupService.getById(Number(groupId)).catch(() => null);
                        let facultyId = cached?.facultyId;
                        if (facultyId === undefined && group) {
                            facultyId = await facultyService.getIdByGroup(group).catch(() => 0) || 0;
                        }
                        schedule_cache[groupIdent] = { data: response.data, timestamp: Date.now(), group, facultyId };
                        await this.sendSchedule(call, schedule_cache[groupIdent]);
                        scheduleService.updateByGroupId(groupId, response.data).catch(e => log.error(`Ошибка при сохранении резервного расписания. groupId:${groupId}`, { stack: e.stack }));
                    } catch (e) {
                        if (e.code === 'NO_SCHEDULE') {
                            await this.handleNoSchedule(call, groupId, e.groupName);
                        } else {
                            await this.handleScheduleError(e, call, groupId);
                        }
                    }
                }
            } else {
                // Обычное переключение дней недели (◀️ / ▶️) или вход в расписание
                if (cached && (now - cached.timestamp < CACHE_MAX_AGE)) {
                    // В пределах 30 минут: мгновенная отдача из памяти, НОЛЬ запросов к API, время НЕ меняется
                    if (!cached.group) {
                        cached.group = await groupService.getById(Number(groupId)).catch(() => null);
                    }
                    if (cached.facultyId === undefined && cached.group) {
                        cached.facultyId = await facultyService.getIdByGroup(cached.group).catch(() => 0) || 0;
                    }
                    bot.answerCallbackQuery(call.id).catch(() => {});
                    await this.sendSchedule(call, cached);
                } else {
                    // Кэш отсутствует или старше 30 минут — скачиваем с сайта
                    try {
                        const response = await downloadSchedule(groupId, language);
                        const group = cached?.group || await groupService.getById(Number(groupId)).catch(() => null);
                        let facultyId = cached?.facultyId;
                        if (facultyId === undefined && group) {
                            facultyId = await facultyService.getIdByGroup(group).catch(() => 0) || 0;
                        }
                        schedule_cache[groupIdent] = { data: response.data, timestamp: Date.now(), group, facultyId };
                        bot.answerCallbackQuery(call.id).catch(() => {});
                        await this.sendSchedule(call, schedule_cache[groupIdent]);
                        scheduleService.updateByGroupId(groupId, response.data).catch(e => log.error(`Ошибка при сохранении расписания в бд. groupId:${groupId}`, { stack: e.stack }));
                    } catch (e) {
                        if (e.code === 'NO_SCHEDULE') {
                            await this.handleNoSchedule(call, groupId, e.groupName);
                        } else {
                            await this.handleScheduleError(e, call, groupId);
                        }
                    }
                }
            }

            // Фоновое обновление пользователя без блокировки UI
            userService.updateUser(call.message.chat.id, {
                userId: call.message.chat.id,
                userType: String(call.message.chat.type),
                userTitle: call.message.chat.title,
                firstName: call.message.chat.first_name,
                lastName: call.message.chat.last_name,
                username: call.message.chat.username,
                group: groupId,
                scheduleType: "student"
            }).then(userOldData => {
                if (userOldData && !userOldData.group) {
                    log.warn(`User ${call.message.chat.id} получил своё первое расписание.`);
                    getAndSendUserInfoByUserId(call.message.chat.id, config.LOG_CHANEL_ID).catch(() => {});
                }
            }).catch((e) => log.error("Ошибка при обновлении данных о пользователе: " + e.message));

            // Логируем действие пользователя только при первом входе или принудительном обновлении
            if (!cached || isRefresh) {
                const groupObj = cached?.group || schedule_cache[groupIdent]?.group;
                const groupName = groupObj?.name ? `"${groupObj.name}"` : `ID ${groupId}`;
                userActionService.logAction(
                    call.message.chat.id,
                    call.message.chat.username,
                    'view_group',
                    `Открыл расписание группы ${groupName}`,
                    { entityId: Number(groupId), entityName: groupObj?.name }
                ).catch(() => {});
            }
        } catch (e) {
            return await unexpectedCallbackErrorController(e, call.message, call.data);
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
