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
import { isKazakhText, normalizeCyrillic } from "../services/teacherDirectoryService.js";

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
            let user_language = await userService.getUserLanguage(chatId).catch(() => 'ru');
            // Если студент написал с казахскими буквами (ә, і, ң, ғ, ү, ұ, қ, ө, һ) — отвечаем на казахском
            if (isKazakhText(text)) {
                user_language = 'kz';
            }

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

            const normQ = normalizeCyrillic(text);
            const compactQuery = normQ.replace(/\s+/g, '');

            // Точным совпадением преподавателя считаем:
            // 1. Если найден ровно 1 преподаватель и 0 групп
            // 2. ИЛИ если запрос полностью совпал с ФИО или инициалами конкретного преподавателя (например "Попова Надежда Викторовна" или "Попова Н. В.")
            let exactTeacher = null;
            if (teachers.length === 1 && groups.length === 0) {
                exactTeacher = teachers[0];
            } else if (teachers.length > 0) {
                const fullExactMatches = teachers.filter(t => {
                    const normName = normalizeCyrillic(t.name);
                    const normFull = normalizeCyrillic(t.fullName || t.name);
                    const aliasMatches = t.aliases && Array.isArray(t.aliases) && t.aliases.some(a => {
                        const nA = normalizeCyrillic(a);
                        return nA === normQ || nA.replace(/\s+/g, '') === compactQuery;
                    });
                    return normFull === normQ || normName === normQ || normFull.replace(/\s+/g, '') === compactQuery || aliasMatches;
                });
                if (fullExactMatches.length === 1) {
                    exactTeacher = fullExactMatches[0];
                }
            }

            // Точным совпадением группы считаем полное совпадение названия
            let exactGroup = null;
            if (groups.length === 1 && teachers.length === 0) {
                exactGroup = groups[0];
            } else if (groups.length > 0) {
                const groupExactMatches = groups.filter(g => {
                    const gNorm = normalizeCyrillic(g.name);
                    return gNorm.replace(/\s+/g, '') === compactQuery || gNorm === normQ;
                });
                if (groupExactMatches.length === 1) {
                    exactGroup = groupExactMatches[0];
                }
            }

            // =========================================================================
            // СЦЕНАРИЙ 1: Точное или единственное совпадение среди преподавателей
            // =========================================================================
            if (exactTeacher && !exactGroup) {
                return await this.openTeacherScheduleDirectly(chatId, msg, exactTeacher, text, user_language);
            }

            // =========================================================================
            // СЦЕНАРИЙ 2: Точное или единственное совпадение среди групп
            // =========================================================================
            if (exactGroup && !exactTeacher) {
                return await this.openGroupScheduleDirectly(chatId, msg, exactGroup, text, user_language);
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
            // СЦЕНАРИЙ 6: Нечёткий поиск при опечатках ("Возможно, вы имели в виду...")
            // =========================================================================
            const fuzzySuggestions = await this.findFuzzySuggestions(text);
            if (fuzzySuggestions && fuzzySuggestions.length > 0) {
                return await this.showFuzzySuggestions(chatId, msg, fuzzySuggestions, text, user_language);
            }

            // =========================================================================
            // СЦЕНАРИЙ 7: Ничего не найдено
            // =========================================================================
            return await this.showNotFound(chatId, text, user_language, msg);

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

        // Интеллектуальное определение приоритета:
        // Если запрос содержит цифры ("ИС 21") или похож на аббревиатуру группы из 2-4 букв ("ИС", "ВТ", "ЮР")
        // и найденные группы начинаются с этого запроса — ГРУППЫ ВЫВОДЯТСЯ ПЕРВЫМИ!
        const normQ = normalizeCyrillic(query);
        const compQ = normQ.replace(/\s+/g, '');
        const hasDigits = /\d/.test(query);
        const isGroupPattern = /^[a-zа-я]{2,4}$/i.test(compQ);
        const groupStartsWithQ = groups.some(g => normalizeCyrillic(g.name).replace(/\s+/g, '').startsWith(compQ));
        const preferGroupsFirst = hasDigits || (isGroupPattern && groupStartsWithQ);

        const maxInline = 5;

        // Блок формирования кнопок преподавателей
        const buildTeachersBlock = () => {
            const topTeachers = teachers.slice(0, maxInline);
            const teacherHeader = user_language === 'kz'
                ? `👨‍🏫 Оқытушылар (${teachers.length}):`
                : `👨‍🏫 Преподаватели (${teachers.length}):`;

            inline_keyboard.push([{ text: teacherHeader, callback_data: 'nothing' }]);
            topTeachers.forEach(t => {
                const label = t.fullName || t.name;
                inline_keyboard.push([{ text: `👨‍🏫 ${label}`, callback_data: `TeacherSchedule|${t.id}|${day}` }]);
            });

            if (teachers.length > maxInline) {
                const cacheKeyT = query.slice(0, 25).trim();
                searchTeacherMenuCache[cacheKeyT] = { teachers, time: Date.now() };
                const moreTeachersText = user_language === 'kz'
                    ? `Барлық оқытушылар (${teachers.length}) ➡️`
                    : `Все преподаватели (${teachers.length}) ➡️`;
                inline_keyboard.push([{ text: moreTeachersText, callback_data: `searchTeacher|${cacheKeyT}|0` }]);
            }
        };

        // Блок формирования кнопок групп
        const buildGroupsBlock = () => {
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
        };

        if (preferGroupsFirst) {
            buildGroupsBlock();
            buildTeachersBlock();
        } else {
            buildTeachersBlock();
            buildGroupsBlock();
        }

        const titleText = user_language === 'kz'
            ? `🔍 «<b>${query}</b>» бойынша нәтижелер:`
            : `🔍 Результаты по запросу «<b>${query}</b>»:`;

        await bot.sendMessage(chatId, titleText, {
            reply_markup: { inline_keyboard },
            parse_mode: 'HTML'
        });
    }

    /**
     * Сообщение, если ничего не найдено по запросу
     */
    async showNotFound(chatId, query, user_language, msg = null) {
        userActionService.logAction(
            chatId,
            msg?.from?.username,
            'smart_search_not_found',
            `Умный поиск: ничего не найдено по запросу "${query}"`
        ).catch(() => {});

        const notFoundText = user_language === 'kz'
            ? `🔍 «<b>${query}</b>» бойынша ештеңе табылмады.\n\n✍️ Оқытушының тегін (мысалы: <i>Попова</i>) немесе топ атауын (мысалы: <i>ИС</i>) жазып көріңіз.`
            : `🔍 По запросу «<b>${query}</b>» ничего не найдено.\n\n✍️ Попробуйте написать фамилию преподавателя (например: <i>Попова</i>) или название группы (например: <i>ИС</i>).`;

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
     * Поиск подсказок при опечатках (Fuzzy search)
     */
    async findFuzzySuggestions(rawQuery) {
        try {
            const clean = String(rawQuery || '').toLowerCase().trim();
            const compact = clean.replace(/[\s-_.,]/g, '');
            if (compact.length < 3) return [];

            const compactNoSuffix = compact.replace(/(\d+)[ркkzru]+$/i, '$1');

            const [allTeachers, allGroups] = await Promise.all([
                TeacherService.getAll().catch(() => []),
                GroupService.getAll().catch(() => [])
            ]);

            const suggestions = [];

            // 1. Поиск среди групп
            for (const g of allGroups) {
                if (!g || !g.name) continue;
                const gComp = g.name.toLowerCase().replace(/[\s-_.,]/g, '');
                const sim1 = calculateSimilarity(compact, gComp);
                const sim2 = calculateSimilarity(compactNoSuffix, gComp);
                const bestSim = Math.max(sim1, sim2);

                if (bestSim >= 0.60) {
                    suggestions.push({ type: 'group', item: g, sim: bestSim });
                }
            }

            // 2. Поиск среди преподавателей
            for (const t of allTeachers) {
                if (!t || !t.name) continue;
                const parts = t.name.toLowerCase().split(/[\s.]+/).filter(Boolean);
                const surname = parts[0] || '';
                const simSurname = calculateSimilarity(compact, surname);
                const simFull = calculateSimilarity(compact, t.name.toLowerCase().replace(/[\s-_.,]/g, ''));
                const bestSim = Math.max(simSurname, simFull);

                if (bestSim >= 0.60) {
                    suggestions.push({ type: 'teacher', item: t, sim: bestSim });
                }
            }

            return suggestions.sort((a, b) => b.sim - a.sim).slice(0, 3);
        } catch (e) {
            log.error(`[SmartSearch] Ошибка fuzzy search: ${e.message}`);
            return [];
        }
    }

    /**
     * Отображение подсказок "Возможно, вы имели в виду..."
     */
    async showFuzzySuggestions(chatId, msg, suggestions, query, user_language) {
        userActionService.logAction(
            chatId,
            msg.from?.username,
            'smart_search_fuzzy_suggest',
            `Умный поиск: предложено ${suggestions.length} вариантов для опечатки "${query}"`
        ).catch(() => {});

        const day = ScheduleController.getCurrentDayNumber();
        const inline_keyboard = [];

        suggestions.forEach(s => {
            if (s.type === 'group') {
                const groupLang = s.item.language || (user_language === 'kz' ? 'каз' : 'рус');
                const btnText = `👥 ${s.item.name} (${groupLang})`;
                inline_keyboard.push([{
                    text: btnText,
                    callback_data: `chooseScheduleLanguage|${groupLang}|${s.item.id}|${day}`
                }]);
            } else if (s.type === 'teacher') {
                const btnText = `👨‍🏫 ${s.item.name}`;
                inline_keyboard.push([{
                    text: btnText,
                    callback_data: `TeacherSchedule|${s.item.id}|${day}`
                }]);
            }
        });

        const titleText = user_language === 'kz'
            ? `🤔 <b>Мүмкін, сіз мынаны іздедіңіз бе?</b>\n\nСұраныс: «<i>${query}</i>»\nКеректісін таңдаңыз 👇`
            : `🤔 <b>Возможно, вы имели в виду:</b>\n\nЗапрос: «<i>${query}</i>»\nНажмите на нужный вариант 👇`;

        await bot.sendMessage(chatId, titleText, {
            reply_markup: { inline_keyboard },
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

function levenshteinDistance(s1, s2) {
    const a = s1.toLowerCase();
    const b = s2.toLowerCase();
    const matrix = [];
    for (let i = 0; i <= b.length; i++) matrix[i] = [i];
    for (let j = 0; j <= a.length; j++) matrix[0][j] = j;

    for (let i = 1; i <= b.length; i++) {
        for (let j = 1; j <= a.length; j++) {
            if (b.charAt(i - 1) === a.charAt(j - 1)) {
                matrix[i][j] = matrix[i - 1][j - 1];
            } else {
                matrix[i][j] = Math.min(
                    matrix[i - 1][j - 1] + 1,
                    matrix[i][j - 1] + 1,
                    matrix[i - 1][j] + 1
                );
            }
        }
    }
    return matrix[b.length][a.length];
}

function calculateSimilarity(s1, s2) {
    const l1 = s1.length;
    const l2 = s2.length;
    if (l1 === 0 || l2 === 0) return 0;
    const dist = levenshteinDistance(s1, s2);
    return 1 - dist / Math.max(l1, l2);
}

export default new SmartSearchController();
