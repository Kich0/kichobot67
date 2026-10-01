import { bot } from "../app.js";
import TeacherService from "../services/teacherService.js";
import GroupService from "../services/groupService.js";
import TeacherScheduleController from "./TeacherScheduleController.js";
import ScheduleController from "./ScheduleController.js";
import SearchTeacherController from "./SearchTeacherController.js";
import SearchGroupController from "./SearchGroupController.js";
import userService from "../services/userService.js";
import userActionService from "../services/userActionService.js";
import i18next from "i18next";
import log from "../logging/logging.js";
import { searchTeacherMenuCache } from "./commands/searchTeacherCommandController.js";
import { searchGroupMenuCache } from "./commands/searchGroupCommandController.js";

class SmartSearchController {
    /**
     * Основная точка входа для свободного текстового поиска:
     * - "Жунусова" -> если 1 препод -> сразу расписание
     * - "Айгуль" -> если несколько преподов -> список кнопок
     * - "ИС-21-1" -> если 1 группа -> сразу расписание
     * - "ИС" / "МАТО" / "У ИС" -> список всех подходящих групп
     * - Если совпали и группы, и преподы -> удобное объединенное меню
     */
    async handleTextSearch(msg) {
        const chatId = msg.chat?.id;
        const text = msg.text?.trim();

        if (!chatId || !text) return;

        try {
            const user_language = await userService.getUserLanguage(chatId).catch(() => 'ru');

            // 1. Валидация: если строка слишком короткая (меньше 2 символов) или состоит только из знаков препинания
            if (text.length < 2 || !/[a-zA-Zа-яА-ЯёЁәіңғүұқөһӘІҢҒҮҰҚӨҺ0-9]/.test(text)) {
                return await this.sendWelcomeFallback(chatId, user_language);
            }

            // 2. Параллельный поиск преподавателей и групп в оперативной памяти (< 1 мс)
            const [teachers, groups] = await Promise.all([
                TeacherService.findByName(text).catch(err => {
                    log.error(`[SmartSearch] Ошибка поиска преподавателей: ${err.message}`);
                    return [];
                }),
                GroupService.findByName(text).catch(err => {
                    log.error(`[SmartSearch] Ошибка поиска групп: ${err.message}`);
                    return [];
                })
            ]);

            const cleanTextLower = text.toLowerCase().trim();
            const compactQuery = cleanTextLower.replace(/[\s-_.,]/g, '');

            const exactTeacher = teachers.find(t => {
                const tComp = t.name.toLowerCase().replace(/[\s-_.,]/g, '');
                return tComp === compactQuery || t.name.toLowerCase() === cleanTextLower;
            });

            const exactGroup = groups.find(g => {
                const gComp = g.name.toLowerCase().replace(/[\s-_.,]/g, '');
                return gComp === compactQuery || g.name.toLowerCase() === cleanTextLower;
            });

            // =========================================================================
            // СЦЕНАРИЙ 1: Точное или единственное совпадение среди преподавателей
            // =========================================================================
            if ((exactTeacher && !exactGroup) || (teachers.length === 1 && groups.length === 0)) {
                const targetTeacher = exactTeacher || teachers[0];
                return await this.openTeacherScheduleDirectly(chatId, msg, targetTeacher, text, user_language);
            }

            // =========================================================================
            // СЦЕНАРИЙ 2: Точное или единственное совпадение среди групп
            // =========================================================================
            if ((exactGroup && !exactTeacher) || (groups.length === 1 && teachers.length === 0)) {
                const targetGroup = exactGroup || groups[0];
                return await this.openGroupScheduleDirectly(chatId, msg, targetGroup, text, user_language);
            }

            // =========================================================================
            // СЦЕНАРИЙ 3: Найдено несколько преподавателей и 0 групп
            // =========================================================================
            if (teachers.length > 0 && groups.length === 0) {
                return await this.showTeachersList(chatId, msg, teachers, text, user_language);
            }

            // =========================================================================
            // СЦЕНАРИЙ 4: Найдено несколько групп и 0 преподавателей (например, "ИС", "МАТО", "У ИС")
            // =========================================================================
            if (groups.length > 0 && teachers.length === 0) {
                return await this.showGroupsList(chatId, msg, groups, text, user_language);
            }

            // =========================================================================
            // СЦЕНАРИЙ 5: Найдено И преподаватели, И группы
            // =========================================================================
            if (teachers.length > 0 && groups.length > 0) {
                return await this.showCombinedResults(chatId, msg, teachers, groups, text, user_language);
            }

            // =========================================================================
            // СЦЕНАРИЙ 6: Ничего не найдено
            // =========================================================================
            return await this.showNotFound(chatId, text, user_language);

        } catch (e) {
            log.error(`[SmartSearch] Непредвиденная ошибка поиска: ${e.message}`, {
                stack: e.stack,
                chatId,
                query: text
            });
            await this.sendWelcomeFallback(chatId, 'ru').catch(() => {});
        }
    }

    /**
     * Мгновенный переход в расписание преподавателя (когда найден 1 препод)
     */
    async openTeacherScheduleDirectly(chatId, msg, teacher, query, user_language) {
        userActionService.logAction(
            chatId,
            msg.from?.username,
            'smart_search_teacher_instant',
            `Умный поиск: мгновенный переход к расписанию преподавателя "${teacher.name}" (запрос: "${query}")`,
            { entityId: Number(teacher.id), entityName: teacher.name }
        ).catch(() => {});

        const answer = await bot.sendMessage(
            chatId,
            `🪄 ${i18next.t('schedule_loading', { lng: user_language })}`,
            { parse_mode: 'HTML' }
        );

        const day = ScheduleController.getCurrentDayNumber();
        const call = {
            id: `text_${Date.now()}`,
            data: `TeacherSchedule|${teacher.id}|${day}`,
            message: answer
        };

        await TeacherScheduleController.getScheduleMenu(call);
    }

    /**
     * Мгновенный переход в расписание группы (когда найдена 1 группа)
     */
    async openGroupScheduleDirectly(chatId, msg, group, query, user_language) {
        userActionService.logAction(
            chatId,
            msg.from?.username,
            'smart_search_group_instant',
            `Умный поиск: мгновенный переход к расписанию группы "${group.name}" (запрос: "${query}")`,
            { entityId: Number(group.id), entityName: group.name }
        ).catch(() => {});

        const answer = await bot.sendMessage(
            chatId,
            `🪄 ${i18next.t('schedule_loading', { lng: user_language })}`,
            { parse_mode: 'HTML' }
        );

        const day = ScheduleController.getCurrentDayNumber();
        const groupLang = group.language || (user_language === 'kz' ? 'каз' : 'рус');
        const call = {
            id: `text_${Date.now()}`,
            data: `chooseScheduleLanguage|${groupLang}|${group.id}|${day}`,
            message: answer
        };

        await ScheduleController.chooseScheduleLanguage(call);
    }

    /**
     * Список преподавателей с пагинацией (когда найдено несколько)
     */
    async showTeachersList(chatId, msg, teachers, query, user_language) {
        userActionService.logAction(
            chatId,
            msg.from?.username,
            'smart_search_teacher_list',
            `Умный поиск: найден список преподавателей (${teachers.length}) по запросу "${query}"`
        ).catch(() => {});

        const cacheKey = query.slice(0, 25).trim();
        searchTeacherMenuCache[cacheKey] = { teachers, time: Date.now() };

        const { data, page, page_count } = ScheduleController.configureMenuData(teachers, 0, user_language);
        const markup = SearchTeacherController.getMenuMarkup(data, cacheKey, page_count, page);

        const msgText = i18next.t('teacher_seactch_success_result', {
            lng: user_language,
            searchQuery: query,
            searchCountResult: teachers.length
        });

        await bot.sendMessage(chatId, msgText, {
            reply_markup: markup,
            parse_mode: 'HTML'
        });
    }

    /**
     * Список групп с пагинацией (когда найдено несколько, например "ИС", "МАТО")
     */
    async showGroupsList(chatId, msg, groups, query, user_language) {
        userActionService.logAction(
            chatId,
            msg.from?.username,
            'smart_search_group_list',
            `Умный поиск: найден список групп (${groups.length}) по запросу "${query}"`
        ).catch(() => {});

        const cacheKey = query.slice(0, 25).trim();
        searchGroupMenuCache[cacheKey] = { groups, time: Date.now() };

        const { data, page, page_count } = ScheduleController.configureMenuData(groups, 0, user_language);
        const markup = SearchGroupController.getMenuMarkup(data, cacheKey, page_count, page);

        const msgText = i18next.t('group_search_success_result', {
            lng: user_language,
            searchQuery: query,
            searchCountResult: groups.length
        });

        await bot.sendMessage(chatId, msgText, {
            reply_markup: markup,
            parse_mode: 'HTML'
        });
    }

    /**
     * Комбинированное меню, если совпали и преподаватели, и группы
     */
    async showCombinedResults(chatId, msg, teachers, groups, query, user_language) {
        userActionService.logAction(
            chatId,
            msg.from?.username,
            'smart_search_combined',
            `Умный поиск: найдены и преподаватели (${teachers.length}), и группы (${groups.length}) по запросу "${query}"`
        ).catch(() => {});

        const day = ScheduleController.getCurrentDayNumber();
        const inline_keyboard = [];

        // 1. Блок преподавателей
        const maxInline = 5;
        const topTeachers = teachers.slice(0, maxInline);
        const teacherHeader = user_language === 'kz'
            ? `👨‍🏫 Оқытушылар (${teachers.length}):`
            : `👨‍🏫 Преподаватели (${teachers.length}):`;

        inline_keyboard.push([{ text: teacherHeader, callback_data: 'nothing' }]);
        topTeachers.forEach(t => {
            inline_keyboard.push([{ text: `👨‍🏫 ${t.name}`, callback_data: `TeacherSchedule|${t.id}|${day}` }]);
        });

        if (teachers.length > maxInline) {
            const cacheKeyT = query.slice(0, 25).trim();
            searchTeacherMenuCache[cacheKeyT] = { teachers, time: Date.now() };
            const moreTeachersText = user_language === 'kz'
                ? `Барлық оқытушылар (${teachers.length}) ➡️`
                : `Все преподаватели (${teachers.length}) ➡️`;
            inline_keyboard.push([{ text: moreTeachersText, callback_data: `searchTeacher|${cacheKeyT}|0` }]);
        }

        // 2. Блок групп
        const topGroups = groups.slice(0, maxInline);
        const groupHeader = user_language === 'kz'
            ? `👥 Топтар (${groups.length}):`
            : `👥 Группы (${groups.length}):`;

        inline_keyboard.push([{ text: groupHeader, callback_data: 'nothing' }]);
        topGroups.forEach(g => {
            const groupLang = g.language || (user_language === 'kz' ? 'каз' : 'рус');
            inline_keyboard.push([{ text: `👥 ${g.name}`, callback_data: `chooseScheduleLanguage|${groupLang}|${g.id}|${day}` }]);
        });

        if (groups.length > maxInline) {
            const cacheKeyG = query.slice(0, 25).trim();
            searchGroupMenuCache[cacheKeyG] = { groups, time: Date.now() };
            const moreGroupsText = user_language === 'kz'
                ? `Барлық топтар (${groups.length}) ➡️`
                : `Все группы (${groups.length}) ➡️`;
            inline_keyboard.push([{ text: moreGroupsText, callback_data: `searchGroup|${cacheKeyG}|0` }]);
        }

        const titleText = user_language === 'kz'
            ? `🔍 «<b>${query}</b>» бойынша оқытушылар да, топтар да табылды:`
            : `🔍 По запросу «<b>${query}</b>» найдены и преподаватели, и группы:`;

        await bot.sendMessage(chatId, titleText, {
            reply_markup: { inline_keyboard },
            parse_mode: 'HTML'
        });
    }

    /**
     * Сообщение, если ничего не найдено по запросу
     */
    async showNotFound(chatId, query, user_language) {
        const notFoundText = user_language === 'kz'
            ? `🔍 «<b>${query}</b>» бойынша ештеңе табылмады.\n\n✍️ Оқытушының тегін (мысалы: <i>Жунусова</i>) немесе топ атауын (мысалы: <i>ИС</i>, <i>МАТО</i>) жазып көріңіз.`
            : `🔍 По запросу «<b>${query}</b>» ничего не найдено.\n\n✍️ Попробуйте написать фамилию преподавателя (например: <i>Жунусова</i>) или название группы (например: <i>ИС</i>, <i>МАТО</i>).`;

        const keyboard = {
            keyboard: [
                [{ text: `${i18next.t('new_schedule', { lng: user_language })}` }, { text: `${i18next.t('help', { lng: user_language })}` }],
                [{ text: `${i18next.t('teacher_schedule', { lng: user_language })}` }, { text: `${i18next.t('student_schedule', { lng: user_language })}` }],
            ],
            one_time_keyboard: false,
            resize_keyboard: true
        };

        await bot.sendMessage(chatId, notFoundText, {
            reply_markup: keyboard,
            parse_mode: 'HTML'
        });
    }

    /**
     * Стандартное приветствие, если введён 1 символ или знак препинания
     */
    async sendWelcomeFallback(chatId, user_language) {
        const keyboard = {
            keyboard: [
                [{ text: `${i18next.t('new_schedule', { lng: user_language })}` }, { text: `${i18next.t('help', { lng: user_language })}` }],
                [{ text: `${i18next.t('teacher_schedule', { lng: user_language })}` }, { text: `${i18next.t('student_schedule', { lng: user_language })}` }],
            ],
            one_time_keyboard: false,
            resize_keyboard: true
        };
        const msgText = i18next.t('welcome_page', { lng: user_language });
        await bot.sendMessage(chatId, msgText, { reply_markup: keyboard, parse_mode: 'HTML' });
    }
}

export default new SmartSearchController();
