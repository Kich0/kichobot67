import ScheduleController from "../controllers/ScheduleController.js";
import log from "../logging/logging.js";
import {callbackAntiSpamMiddleware} from "../middlewares/bot/callbackAntiSpamMiddleware.js";
import ProfileController from "../controllers/ProfileController.js";
import {queryValidationErrorController} from "../exceptions/bot/queryValidationErrorController.js";
import {unexpectedCallbackErrorController} from "../exceptions/bot/unexpectedCallbackErrorController.js";
import TeacherScheduleController from "../controllers/TeacherScheduleController.js";
import {bot} from "../app.js";
import SearchGroupController from "../controllers/SearchGroupController.js";
import SearchTeacherController from "../controllers/SearchTeacherController.js";
import {redirectToNewScheduleMenu} from "../controllers/commands/newScheduleCommandController.js";
import userService from "../services/userService.js";
import {welcomePageRedirectController} from "../controllers/commands/startCommandController.js";
import userActionService from "../services/userActionService.js";

export default function setupCallbackHandlers() {
    bot.on('callback_query', async (call) => {
        log.silly(`User ${call.message.chat.id} clicked to btn ${call.data}`, {call, userId: call.message.chat.id});
        await callbackAntiSpamMiddleware(call, async () => {
            // Мгновенный ответ Telegram (Fast Ack) — гасит крутящийся спиннер за 25-40 мс
            bot.answerCallbackQuery(call.id).catch(() => {});

            const chatId = call.message?.chat?.id || call.from?.id;
            const username = call.from?.username || call.message?.chat?.username;

            if (call.data === "nothing") {
                return;
            }

            if (call.data === "delete") {
                userActionService.logAction(chatId, username, 'delete_message', 'Удалил сообщение бота').catch(() => {});
                await bot.deleteMessage(call.message.chat.id, call.message.message_id)
                    .catch((e) => log.warn(`User ${call.message.chat.id} получил ошибку при попытке удалить менюшку. Юзер никак не пострадал.` + e.message, {stack: e.stack}));
                return;
            }

            if (call.data === 'start'){
                userActionService.logAction(chatId, username, 'start_menu', 'Вернулся на главный экран выбора расписания').catch(() => {});
                await redirectToNewScheduleMenu(call.message);
                return;
            }

            if (call.data.startsWith("faculty|")) {
                try {
                    const [, page] = call.data.split('|');
                    if (isNaN(parseFloat(page))) {
                        return await queryValidationErrorController(call);
                    }
                    const pageNum = +page + 1;
                    userActionService.logAction(chatId, username, 'faculty_menu', `Выбор факультета (стр. ${pageNum})`).catch(() => {});
                    await ScheduleController.getFacultyMenu(call.message, +page);
                } catch (e) {
                    return await unexpectedCallbackErrorController(e, call.message, call.data);
                }
                return;
            }

            if (call.data.startsWith("program|")) {
                try {
                    const [, facultyId, page] = call.data.split('|');
                    if (!facultyId || !page) {
                        return await queryValidationErrorController(call);
                    }
                    const pageNum = isNaN(parseFloat(page)) ? 1 : +page + 1;
                    userActionService.logAction(chatId, username, 'program_menu', `Выбор специальности (факультет ${facultyId}, стр. ${pageNum})`, { entityId: Number(facultyId) }).catch(() => {});
                    await ScheduleController.getProgramMenu(call.message, facultyId, +page);
                } catch (e) {
                    return await unexpectedCallbackErrorController(e, call.message, call.data);
                }
                return;
            }

            if (call.data.startsWith("group|")) {
                try {
                    const [, facultyId, programId, page] = call.data.split('|');
                    if (!facultyId || !programId || !page) {
                        return await queryValidationErrorController(call);
                    }
                    const pageNum = isNaN(parseFloat(page)) ? 1 : +page + 1;
                    userActionService.logAction(chatId, username, 'group_menu', `Выбор группы (специальность ${programId}, стр. ${pageNum})`, { entityId: Number(programId) }).catch(() => {});
                    await ScheduleController.getGroupMenu(call.message, programId, facultyId, +page);
                } catch (e) {
                    return await unexpectedCallbackErrorController(e, call.message, call.data);
                }
                return;
            }

            if (call.data.includes("schedule|")) {
                if (call.data.split("|").length < 2) {
                    return await queryValidationErrorController(call);
                }

                const [, , groupId,] = call.data.split("|");
                if (!groupId) {
                    return await queryValidationErrorController(call);
                }
                const isRefresh = call.data.includes("refresh");
                if (isRefresh) {
                    call.data = call.data.replace('refresh', '');
                }
                try {
                    await ScheduleController.getScheduleMenu(call, isRefresh);
                } catch (e) {
                    console.error(e);
                    log.error("ОШИБКА В КОЛБЕК ХЕНДЕЛЕРЕ schedule", {userId: call.message.chat.id, stack: e.stack});
                }
                return;
            }

            if (call.data.startsWith("chooseScheduleLanguage|")){
                const [, , groupId,] = call.data.split("|");

                try{
                    await ScheduleController.chooseScheduleLanguage(call);
                }catch (e){
                    console.error(e);
                    log.error("ОШИБКА В КОЛБЕК ХЕНДЕЛЕРЕ chooseScheduleLanguage", {userId: call.message.chat.id, stack: e.stack});
                }
                return;
            }

            if (call.data.startsWith("profile|")) {
                try {
                    const [, _id] = call.data.split("|");
                    userActionService.logAction(chatId, username, 'view_profile', 'Открыл профиль преподавателя', { entityName: _id }).catch(() => {});
                    await ProfileController.getProfile(call, _id);
                } catch (e) {
                    console.error(e);
                    log.error("ВАЖНО! ОШИБКА В PROFILE КОЛБЕК ХЕНДЛЕРЕ!", {
                        userId: call.message.chat.id,
                        stack: e.stack
                    });
                }
                return;
            }

            if (call.data.startsWith("department|")) {
                try {
                    const [, page] = call.data.split('|');
                    if (isNaN(parseFloat(page))) {
                        return await queryValidationErrorController(call);
                    }
                    const pageNum = +page + 1;
                    userActionService.logAction(chatId, username, 'department_menu', `Выбор кафедры (стр. ${pageNum})`).catch(() => {});
                    await TeacherScheduleController.getDepartmentMenu(call.message, +page);
                } catch (e) {
                    return await unexpectedCallbackErrorController(e, call.message, call.data);
                }
                return;
            }

            if (call.data.startsWith("teacherImg|") || call.data.startsWith("refreshteacherImg|")) {
                const isRefresh = call.data.startsWith("refreshteacherImg|");
                try {
                    await TeacherScheduleController.sendScheduleImage(call, isRefresh);
                } catch (e) {
                    bot.answerCallbackQuery(call.id).catch(() => {});
                    log.error("ОШИБКА В КОЛБЕК ХЕНДЛЕРЕ teacherImg", {
                        userId: call.message.chat.id,
                        stack: e.stack
                    });
                }
                return;
            }

            if (call.data.startsWith("teacherText|")) {
                bot.answerCallbackQuery(call.id).catch(() => {});
                try {
                    await TeacherScheduleController.sendScheduleFromImage(call);
                } catch (e) {
                    log.error("ОШИБКА В КОЛБЕК ХЕНДЛЕРЕ teacherText", {
                        userId: call.message.chat.id,
                        stack: e.stack
                    });
                }
                return;
            }

            if (call.data.startsWith("teacher|")) {
                try {
                    const [, departmentId, page] = call.data.split('|');
                    if (isNaN(parseFloat(page))) {
                        return await queryValidationErrorController(call);
                    }
                    const pageNum = +page + 1;
                    userActionService.logAction(chatId, username, 'teacher_menu', `Выбор преподавателя (кафедра ${departmentId}, стр. ${pageNum})`, { entityId: Number(departmentId) }).catch(() => {});
                    await TeacherScheduleController.getTeacherMenu(call.message, departmentId, +page);
                } catch (e) {
                    return await unexpectedCallbackErrorController(e, call.message, call.data);
                }
                return;
            }

            if (call.data.includes("TeacherSchedule|")) {
                const [, teacherId,] = call.data.split("|");
                if (!teacherId) {
                    return await queryValidationErrorController(call);
                }
                const isRefresh = call.data.includes("refresh");
                if (isRefresh) {
                    call.data = call.data.replace('refresh', '');
                }
                try {
                    await TeacherScheduleController.getScheduleMenu(call, isRefresh);
                } catch (e) {
                    console.error(e);
                    log.error("ОШИБКА В КОЛБЕК ХЕНДЕЛЕРЕ teacherSchedule", {
                        userId: call.message.chat.id,
                        stack: e.stack
                    });
                }
                return;
            }

            if (call.data.startsWith("searchGroup|")) {
                try {
                    const [, groupName, page] = call.data.split("|");
                    const pageNum = isNaN(parseFloat(page)) ? 1 : +page + 1;
                    userActionService.logAction(chatId, username, 'search_group_page', `Пагинация поиска групп: "${groupName}" (стр. ${pageNum})`).catch(() => {});
                    await SearchGroupController.getSearchGroupMenu(call.message, groupName, +page);
                } catch (e) {
                    return await unexpectedCallbackErrorController(e, call.message, call.data);
                }
                return;
            }

            if (call.data.startsWith("searchTeacher|")) {
                try {
                    const [, teacher, page] = call.data.split("|");
                    const pageNum = isNaN(parseFloat(page)) ? 1 : +page + 1;
                    userActionService.logAction(chatId, username, 'search_teacher_page', `Пагинация поиска преподавателей: "${teacher}" (стр. ${pageNum})`).catch(() => {});
                    await SearchTeacherController.getSearchTeacherMenu(call.message, teacher, +page);
                } catch (e) {
                    return await unexpectedCallbackErrorController(e, call.message, call.data);
                }
                return;
            }

            if (call.data.startsWith("languageIs")){
                try{
                    const language = call.data.replace("languageIs", "").toLowerCase();
                    userActionService.logAction(chatId, username, 'change_language', `Сменил язык на ${language === 'kz' ? 'казахский' : 'русский'}`).catch(() => {});
                    await userService.setUserLanguage(call.message.chat.id, language);

                    await welcomePageRedirectController(call);
                }catch (e) {
                    return await unexpectedCallbackErrorController(e, call.message, call.data);
                }
                return;
            }
        });
    });
}
