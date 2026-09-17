import departmentService from "../services/departmentService.js";
import ScheduleController, {schedule_cache} from "./ScheduleController.js";
import teacherService from "../services/teacherService.js";
import log from "../logging/logging.js";
import {unexpectedCallbackErrorController} from "../exceptions/bot/unexpectedCallbackErrorController.js";
import userService from "../services/userService.js";
import teacherScheduleService from "../services/teacherScheduleService.js";
import {bot} from "../app.js";
import i18next from 'i18next'
import config from "../config.js";
import userActionService from "../services/userActionService.js";
import { enrichTeacherSchedule } from "../services/teacherScheduleEnricher.js";
import TeacherTableImageService from "../services/TeacherTableImageService.js";
import ScheduleApiAdapter from "../services/scheduleApiAdapter.js";
// ПРЯМОЙ ИМПОРТ бэкенд-сервиса вместо HTTP
import BackendTeacherScheduleService from "../../backend/services/TeacherScheduleService.js";

async function downloadSchedule(teacherId, attemption = 1) {
    try {
        // Прямой вызов вместо axios.get(KSU_HELPER_URL/...)
        // Возвращаем объект с .data для совместимости с остальным кодом
        const data = await BackendTeacherScheduleService.get_teacher_schedule(teacherId);
        return { data, status: 200 };
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
        const regex = /\s*\(([^/]+)\/([^)]+)\)/g;
        let resultString = inputString.replace(regex, ' (Ауд. $1 | $2 корпус)');
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

    normalizeTime(timeStr) {
        if (!timeStr) return '';
        return timeStr
            .replace(/\s+/g, '')
            .replace(/:/g, '.')
            .replace(/[-–—]/g, '-')
            .replace(/(^|-)0(\d)/g, '$1$2');
    }

    formatSubjectWithLessonType(subject, lessonType) {
        if (!subject) return '';
        let cleanSubject = subject.trim();

        // Заменяем скобочные или слэшевые сокращения типа (лек), (пр), /лек/, /пр/ на полноценные понятные обозначения
        cleanSubject = cleanSubject
            .replace(/\s*\((?:лекц?|лек)\)/gi, ' /Лекция/')
            .replace(/\s*\/(?:лекц?|лек)\//gi, ' /Лекция/')
            .replace(/\s*\((?:прак(?:тическое)?|семин(?:ар)?|пр)\)/gi, ' /Прак.зан./')
            .replace(/\s*\/(?:прак(?:тическое)?|семин(?:ар)?|пр)\//gi, ' /Прак.зан./')
            .replace(/\s*\((?:лаб(?:ораторное)?|лаб)\)/gi, ' /Лаб.раб./')
            .replace(/\s*\/(?:лаб(?:ораторное)?|лаб)\//gi, ' /Лаб.раб./')
            .replace(/\s*\((?:сроп)\)/gi, ' /СРОП/')
            .replace(/\s*\/(?:сроп)\//gi, ' /СРОП/');

        // Проверяем, есть ли уже тип занятия в названии предмета
        const hasTypeAlready = /\/(?:Лекция|Прак\.зан\.|Лаб\.раб\.|СРОП)\//i.test(cleanSubject);
        if (!hasTypeAlready && lessonType) {
            const lower = lessonType.toLowerCase().trim();
            let typeBadge = '';
            if (lower.includes('лекц') || lower === 'лек') typeBadge = '/Лекция/';
            else if (lower.includes('прак') || lower.includes('семин') || lower === 'пр') typeBadge = '/Прак.зан./';
            else if (lower.includes('лаб')) typeBadge = '/Лаб.раб./';
            else if (lower.includes('сроп')) typeBadge = '/СРОП/';
            else if (lessonType.trim()) typeBadge = `/${lessonType.trim()}/`;

            if (typeBadge) {
                cleanSubject = `${cleanSubject} ${typeBadge}`;
            }
        }

        return cleanSubject.trim();
    }

    formatElapsedTime(timestamp, user_language) {
        return ScheduleController.formatElapsedTime(timestamp, user_language);
    }

    formatTimestamp(timestamp) {
        return ScheduleController.formatTimestamp(timestamp);
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

            if (msgToEdit.photo) {
                await bot.deleteMessage(msgToEdit.chat.id, msgToEdit.message_id).catch(() => {});
                await bot.sendMessage(msgToEdit.chat.id, msgText, { reply_markup: markup });
            } else {
                await bot.editMessageText(msgText, {
                    chat_id: msgToEdit.chat.id, message_id: msgToEdit.message_id, reply_markup: markup
                });
            }
        } catch (e) {
            throw e
        }
    }

    async sendSchedule(call, schedule_cache, preMessage = '') {
        try {
            const user_language = await userService.getUserLanguage(call.message.chat.id)

            const timestamp = schedule_cache.timestamp
            const data = schedule_cache.data
            let teacher = schedule_cache.teacher

            const data_array = call.data.split('|');
            let [, teacherId, dayNumber] = data_array
            if (+dayNumber > 5) {
                dayNumber = 0
            }
            if (+dayNumber < 0) {
                dayNumber = 5
            }

            // Если teacher не было в объекте кэша — пробуем подгрузить из базы
            if (!teacher && teacherId) {
                teacher = await teacherService.getById(teacherId).catch(() => null);
                if (teacher) {
                    schedule_cache.teacher = teacher;
                }
            }

            const scheduleLifeTime = ScheduleController.formatElapsedTime(timestamp, user_language)
            const scheduleDateTime = ScheduleController.formatTimestamp(timestamp)

            const schedule_day = data[dayNumber]['day']
            const preSchedule = data[dayNumber]['groups']

            const schedule = preSchedule.filter(obj => obj.group !== '')

            // Дедупликация слотов по времени (если в данных случайно есть дубли)
            const deduplicated = [];
            const seenTimes = new Map();
            for (const item of schedule) {
                const normTime = this.normalizeTime(item.time);
                if (seenTimes.has(normTime)) {
                    const existing = seenTimes.get(normTime);
                    if (item.group && !existing.group.includes(item.group)) {
                        existing.group = `${existing.group}, ${item.group}`;
                    }
                    if (!existing.subject && item.subject) {
                        existing.subject = item.subject;
                        existing.lessonType = item.lessonType;
                    }
                } else {
                    const copy = { ...item };
                    seenTimes.set(normTime, copy);
                    deduplicated.push(copy);
                }
            }

            let schedule_text = ``
            const teacherName = teacher?.name || `ID ${teacherId || ''}`
            const headerText = `👥 <u>${teacherName}</u>\n📆 ${i18next.t('schedule_by_day', { lng: user_language, dayName: schedule_day })}\n`

            if (!deduplicated.length) {
                schedule_text = `🥳 <b>${i18next.t('vacation', { lng: user_language })}</b>\n`
            } else {
                const formattedItems = deduplicated.map(item => {
                    let text = '⌚️ ' + item.time + '\n';
                    const formattedGroup = this.transformGroupString(item.group);
                    text += this.addSymbolToEachLine(formattedGroup, '👥') + '\n';
                    if (item.subject) {
                        const formattedSubject = this.formatSubjectWithLessonType(item.subject, item.lessonType);
                        text += `📖 <i>${formattedSubject}</i>\n`;
                    }
                    return text.trimEnd();
                });
                schedule_text = formattedItems.join('\n\n') + '\n';
            }
            let end_text = `🕰 <i><b>${i18next.t('schedule_downloaded', {lng:user_language, timeAgo:scheduleLifeTime})} || ${scheduleDateTime}</b></i>\n` +
                `${i18next.t('for_help', {lng:user_language})}\n` +
                `${i18next.t('official_site_compare', {lng:user_language})}\n`

            let msg_text = preMessage + headerText + schedule_text + end_text

            const preCallback = data_array.slice(0, -1).join("|")
            const departmentId = teacher?.department || 0;

            let markup = {
                inline_keyboard: [
                    [{ text: `⬅️${i18next.t('go_back', {lng:user_language})}`, callback_data: preCallback + `|${+dayNumber - 1}` }, {
                        text: `🔄`,
                        callback_data: 'refresh' + call.data
                    }, {
                        text: `${i18next.t('go_forward', {lng:user_language})}➡️`, callback_data: preCallback + `|${+dayNumber + 1}`
                    }],
                    [{ text: `📊 ${i18next.t('schedule_table_week', { lng: user_language })}`, callback_data: `teacherImg|${teacherId}|${dayNumber}` }],
                    [{ text: `🔙 ${i18next.t('go_prev_menu', { lng: user_language })}`, callback_data: `teacher|${departmentId}|0` }]
                ]
            }

            if (call.message.photo) {
                await bot.deleteMessage(call.message.chat.id, call.message.message_id).catch(() => {});
                await bot.sendMessage(call.message.chat.id, msg_text, {
                    parse_mode: "HTML",
                    reply_markup: markup,
                    disable_web_page_preview: true
                });
            } else {
                await bot.editMessageText(msg_text,
                    {
                        message_id: call.message.message_id,
                        chat_id: call.message.chat.id,
                        parse_mode: "HTML",
                        reply_markup: markup,
                        disable_web_page_preview: true
                    });
            }
        } catch (e) {
            await unexpectedCallbackErrorController(e, call.message, call.data)
        }
    }

    async getScheduleMenu(call, forceRefresh = false) {
        try {
            const isRefresh = forceRefresh || call.data.includes("refresh");
            if (call.data.includes("refresh")) {
                call.data = call.data.replace('refresh', '');
            }
            const data_array = call.data.split('|');
            let [, teacherId] = data_array

            const cached = schedule_cache[teacherId];
            const now = Date.now();
            const FRESH_TTL = 5 * 60 * 1000;    // 5 мин — кэш свежий (кулдаун на 5 мин)
            const STALE_TTL = 30 * 60 * 1000;   // 30 мин — мгновенная отдача + тихий фоновый ETag-запрос

            if (cached && (now - cached.timestamp <= FRESH_TTL)) {
                if (!cached.teacher) {
                    cached.teacher = await teacherService.getById(teacherId).catch(() => null);
                }
                const hasMissingSubjects = cached.data?.some(d => d.groups?.some(g => g.group && !g.subject));
                if (hasMissingSubjects && !cached._enriched) {
                    cached.data = await enrichTeacherSchedule(cached.data, cached.teacher);
                    cached._enriched = true;
                    teacherScheduleService.updateByTeacherId(teacherId, cached.data).catch(() => {});
                    TeacherTableImageService.invalidateTeacherImage(teacherId);
                }
                // Кэш свежий — показываем мгновенно (при клике refresh обновятся секунды "X секунд назад")
                await this.sendSchedule(call, cached);
            } else if (!isRefresh && cached && (now - cached.timestamp <= STALE_TTL)) {
                if (!cached.teacher) {
                    cached.teacher = await teacherService.getById(teacherId).catch(() => null);
                }
                const hasMissingSubjects = cached.data?.some(d => d.groups?.some(g => g.group && !g.subject));
                if (hasMissingSubjects && !cached._enriched) {
                    cached.data = await enrichTeacherSchedule(cached.data, cached.teacher);
                    cached._enriched = true;
                    teacherScheduleService.updateByTeacherId(teacherId, cached.data).catch(() => {});
                    TeacherTableImageService.invalidateTeacherImage(teacherId);
                }
                // Stale-while-revalidate: показываем старый кэш, обновляем в фоне
                await this.sendSchedule(call, cached);
                // Фоновое обновление (не ждём результат)
                downloadSchedule(teacherId)
                    .then(async (response) => {
                        const teacher = await teacherService.getById(teacherId).catch(() => null);
                        const enrichedData = await enrichTeacherSchedule(response.data, teacher);
                        schedule_cache[teacherId] = { data: enrichedData, timestamp: Date.now(), teacher, _enriched: true };
                        TeacherTableImageService.invalidateTeacherImage(teacherId);
                        await teacherScheduleService.updateByTeacherId(teacherId, enrichedData).catch(e => log.error(`Ошибка при сохранении резервного teacher расписания. teacherId:${teacherId}`, {
                            stack: e.stack
                        }));
                        log.info(`[Stale-Revalidate] Расписание для преподавателя ${teacherId} обновлено в фоне`);
                    })
                    .catch(e => log.warn(`[Stale-Revalidate] Не удалось обновить расписание преподавателя в фоне: ${e.message}`));
            } else {
                // Если кликнули обновить после 1 минуты или кэш старше 15 минут — скачиваем свежее расписание
                if (isRefresh) {
                    TeacherTableImageService.invalidateTeacherImage(teacherId);
                }
                await downloadSchedule(teacherId)
                    .then(async (response) => {
                        const teacher = await teacherService.getById(teacherId).catch(() => null)
                        const enrichedData = await enrichTeacherSchedule(response.data, teacher);
                        schedule_cache[teacherId] = { data: enrichedData, timestamp: Date.now(), teacher, _enriched: true }
                        TeacherTableImageService.invalidateTeacherImage(teacherId);
                        await this.sendSchedule(call, schedule_cache[teacherId])

                        await teacherScheduleService.updateByTeacherId(teacherId, enrichedData).catch(e => log.error(`Ошибка при попытке сохранить резервную копию teacher расписания в бд. teacherId:${teacherId}. Пользователь никак не пострадал.`, {
                            stack: e.stack, call, userId: call.message.chat.id
                        }))
                    })
                    .catch(async (e) => {
                        try {
                            const user_language = await userService.getUserLanguage(call.message.chat.id)

                            let error_text = "⚠️ Произошла непредвиденная ошибка. Не получилось загрузить ваше расписание. Попробуйте обновить расписание."
                            if (e.response) {
                                if (e.response.status === 503)
                                    error_text = i18next.t('schedule_error_503', {lng: user_language})

                                if (e.response.status === 500) {
                                    error_text = i18next.t('schedule_error_500', {lng: user_language})
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

            // Понятное логирование действия на русском языке
            teacherService.getById(teacherId).then(tch => {
                const teacherName = tch?.name ? `"${tch.name}"` : `ID ${teacherId}`;
                userActionService.logAction(
                    call.message.chat.id,
                    call.message.chat.username,
                    'view_teacher',
                    `Открыл расписание преподавателя ${teacherName}`,
                    { entityId: Number(teacherId), entityName: tch?.name }
                );
            }).catch(() => {});

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
            const hasMissingSubjects = response.data?.some(d => d.groups?.some(g => g.group && !g.subject));
            let data = response.data;
            if (hasMissingSubjects) {
                data = await enrichTeacherSchedule(data, teacher);
                teacherScheduleService.updateByTeacherId(teacherId, data).catch(() => {});
                TeacherTableImageService.invalidateTeacherImage(teacherId);
            }
            schedule_cache[teacherId] = {data, timestamp, teacher, _enriched: true}
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

    async sendScheduleImage(call, forceRefresh = false) {
        try {
            const user_language = await userService.getUserLanguage(call.message.chat.id);
            const data_array = call.data.split('|');
            let [, teacherId, dayNumber = 0] = data_array;

            const now = Date.now();
            const FRESH_TTL = 5 * 60 * 1000;
            let cached = schedule_cache[teacherId];

            if (forceRefresh && cached && (now - cached.timestamp <= FRESH_TTL)) {
                // Если меньше 5 минут: просто обновляем секунды в подписи фото
                const timestamp = cached.timestamp;
                const scheduleLifeTime = ScheduleController.formatElapsedTime(timestamp, user_language);
                const scheduleDateTime = ScheduleController.formatTimestamp(timestamp);
                const timeString = `${scheduleLifeTime} || ${scheduleDateTime}`;
                const teacher = cached.teacher || (await teacherService.getById(teacherId).catch(() => null));
                const teacherName = teacher?.name || `ID ${teacherId}`;
                const caption = `${i18next.t('teacher_grid_caption', { lng: user_language, teacherName })}\n\n🕒 <i><b>${timeString}</b></i>`;
                const departmentId = teacher?.department || 0;
                const markup = {
                    inline_keyboard: [
                        [{ text: `📝 ${i18next.t('schedule_text_view', { lng: user_language })}`, callback_data: `teacherText|${teacherId}|${dayNumber}` }],
                        [{ text: `🔄`, callback_data: `refreshteacherImg|${teacherId}|${dayNumber}` }],
                        [{ text: `🔙 ${i18next.t('go_prev_menu', { lng: user_language })}`, callback_data: `teacher|${departmentId}|0` }]
                    ]
                };
                if (call.message.photo) {
                    await bot.editMessageCaption(caption, {
                        chat_id: call.message.chat.id,
                        message_id: call.message.message_id,
                        parse_mode: 'HTML',
                        reply_markup: markup
                    }).catch(() => {});
                    return;
                }
            }

            if (forceRefresh) {
                TeacherTableImageService.invalidateTeacherImage(teacherId);
                delete schedule_cache[teacherId];
                cached = null;
            }

            let data = cached?.data;
            let teacher = cached?.teacher;

            if (!teacher) {
                teacher = await teacherService.getById(teacherId).catch(() => null);
            }

            if (!data) {
                const doc = await teacherScheduleService.getByTeacherId(teacherId).catch(() => null);
                if (doc && Array.isArray(doc.data) && doc.data.length > 0) {
                    data = doc.data;
                    cached = { data, timestamp: new Date(doc.updatedAt).getTime(), teacher };
                    schedule_cache[teacherId] = cached;
                } else {
                    try {
                        const response = await downloadSchedule(teacherId);
                        data = response.data;
                        cached = { data, timestamp: Date.now(), teacher };
                        schedule_cache[teacherId] = cached;
                    } catch (downloadErr) {
                        log.warn(`Не удалось загрузить расписание для преподавателя ${teacherId}: ${downloadErr.message}`);
                        if (doc && Array.isArray(doc.data)) {
                            data = doc.data;
                        } else {
                            data = ScheduleApiAdapter.adaptTeacherSchedule([]);
                        }
                        cached = { data, timestamp: Date.now(), teacher };
                        schedule_cache[teacherId] = cached;
                    }
                }
            }

            const hasMissingSubjects = data?.some(d => d.groups?.some(g => g.group && !g.subject));
            if (hasMissingSubjects && !cached?._enriched) {
                data = await enrichTeacherSchedule(data, teacher);
                if (cached) {
                    cached.data = data;
                    cached._enriched = true;
                }
                teacherScheduleService.updateByTeacherId(teacherId, data).catch(() => {});
                TeacherTableImageService.invalidateTeacherImage(teacherId);
            }

            const timestamp = cached?.timestamp || Date.now();
            const scheduleLifeTime = ScheduleController.formatElapsedTime(timestamp, user_language);
            const scheduleDateTime = ScheduleController.formatTimestamp(timestamp);
            const timeString = `${scheduleLifeTime} || ${scheduleDateTime}`;

            const pngBuffer = await TeacherTableImageService.getTeacherTableImage(teacher, data, user_language);

            const teacherName = teacher?.name || `ID ${teacherId}`;
            const caption = `${i18next.t('teacher_grid_caption', { lng: user_language, teacherName })}\n\n🕒 <i><b>${timeString}</b></i>`;
            const departmentId = teacher?.department || 0;

            const markup = {
                inline_keyboard: [
                    [{ text: `📝 ${i18next.t('schedule_text_view', { lng: user_language })}`, callback_data: `teacherText|${teacherId}|${dayNumber}` }],
                    [{ text: `🔄`, callback_data: `refreshteacherImg|${teacherId}|${dayNumber}` }],
                    [{ text: `🔙 ${i18next.t('go_prev_menu', { lng: user_language })}`, callback_data: `teacher|${departmentId}|0` }]
                ]
            };

            const photoMsg = await bot.sendPhoto(call.message.chat.id, pngBuffer, {
                caption,
                parse_mode: 'HTML',
                reply_markup: markup
            }, {
                filename: 'teacher_schedule.png',
                contentType: 'image/png'
            });

            if (photoMsg) {
                await bot.deleteMessage(call.message.chat.id, call.message.message_id).catch(() => {});
            }
        } catch (e) {
            log.error('Ошибка генерации изображения расписания преподавателя', {
                stack: e.stack,
                teacherId: call.data
            });
            await unexpectedCallbackErrorController(e, call.message, call.data);
        }
    }

    async sendScheduleFromImage(call) {
        try {
            const data_array = call.data.split('|');
            let [, teacherId, dayNumber = 0] = data_array;
            const textCall = {
                ...call,
                data: `TeacherSchedule|${teacherId}|${dayNumber}`
            };
            let cached = schedule_cache[teacherId];
            if (!cached) {
                const doc = await teacherScheduleService.getByTeacherId(teacherId);
                const teacher = await teacherService.getById(teacherId).catch(() => null);
                if (doc) {
                    cached = { data: doc.data, timestamp: new Date(doc.updatedAt).getTime(), teacher };
                    schedule_cache[teacherId] = cached;
                }
            }
            if (cached) {
                const hasMissingSubjects = cached.data?.some(d => d.groups?.some(g => g.group && !g.subject));
                if (hasMissingSubjects && !cached._enriched) {
                    cached.data = await enrichTeacherSchedule(cached.data, cached.teacher);
                    cached._enriched = true;
                    teacherScheduleService.updateByTeacherId(teacherId, cached.data).catch(() => {});
                    TeacherTableImageService.invalidateTeacherImage(teacherId);
                }
                await this.sendSchedule(textCall, cached);
            } else {
                await this.getScheduleMenu(textCall);
            }
        } catch (e) {
            await unexpectedCallbackErrorController(e, call.message, call.data);
        }
    }
}

export default new TeacherScheduleController()