import { Group } from "../models/group.js";
import { Schedule } from "../models/schedule.js";
import log from "../logging/logging.js";
import BackendScheduleService from "../../backend/services/ScheduleService.js";

/**
 * Нормализовать строку времени для надёжного сравнения:
 * убирает пробелы, заменяет двоеточия и разные тире, убирает ведущие нули:
 * "08.30-09.20", "8:30 - 09:20", "08.30–9.20" -> "8.30-9.20"
 */
function normalizeTime(timeStr) {
    if (!timeStr) return '';
    return timeStr
        .replace(/\s+/g, '')
        .replace(/:/g, '.')
        .replace(/[-–—]/g, '-')
        .replace(/(^|-)0(\d)/g, '$1$2');
}

/**
 * Замена латинских букв-двойников на кириллические (на сайте вуза встречаются опечатки вроде "аcсис" с латинской "c")
 */
function normalizeHomoglyphs(str) {
    return str
        .replace(/a/gi, 'а')
        .replace(/c/gi, 'с')
        .replace(/e/gi, 'е')
        .replace(/o/gi, 'о')
        .replace(/p/gi, 'р')
        .replace(/x/gi, 'х')
        .replace(/k/gi, 'к');
}

/**
 * Извлечь фамилию преподавателя для сопоставления в ячейках расписания группы
 */
function extractSurnameToken(fullName) {
    if (!fullName) return '';
    // Игнорируем академические звания и должности
    const ignoreTokens = new Set([
        'ст', 'пр', 'преп', 'доц', 'проф', 'ассоц', 'ассис', 'ассиспроф', 'ассоцпроф',
        'phd', 'магистр', 'бакалавр', 'доцент', 'профессор', 'преподаватель', 'асс',
        'зав', 'каф', 'декан', 'зам', 'ио', 'докт', 'канд', 'др', 'мн', 'сн'
    ]);
    // Разбиваем по пробелам и знакам препинания (., / \ ( ) _ -)
    const words = fullName
        .split(/[\s.,/\\()_-]+/)
        .map(w => w.replace(/[^А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІіA-Za-z]/g, ''))
        .filter(Boolean);

    // Ищем первое слово от 2 букв, не являющееся должностью/званием
    const candidate = words.find(w => w.length >= 2 && !ignoreTokens.has(normalizeHomoglyphs(w.toLowerCase())));
    return candidate || '';
}

/**
 * Распарсить строку предмета из расписания группы вида:
 * "Название предмета /Тип занятия/ Звание Имя Ауд."
 */
function parseSubjectLine(line) {
    if (!line) return null;
    const trimmed = line.trim();
    const match = trimmed.match(/^(.*?)\s*\/(.*?)\/\s*(.*)$/);
    if (match) {
        return {
            subject: match[1].trim(),
            lessonType: match[2].trim()
        };
    }
    return {
        subject: trimmed,
        lessonType: ''
    };
}

/**
 * Обогатить расписание преподавателя названиями предметов и типов пар
 * на основе расписаний соответствующих студенческих групп.
 *
 * @param {Array} scheduleData - массив дней с группами расписания преподавателя
 * @param {Object|string} teacher - объект преподавателя или его ФИО
 * @returns {Promise<Array>} - обогащённый массив дней
 */
export async function enrichTeacherSchedule(scheduleData, teacher) {
    if (!scheduleData || !Array.isArray(scheduleData)) return scheduleData;

    try {
        const teacherName = typeof teacher === 'string' ? teacher : (teacher?.name || '');
        const surnameToken = extractSurnameToken(teacherName);

        // 1. Собрать все уникальные имена групп из расписания
        const groupRegex = /([^\s()]+)\s*\(([^)]+)\)/g;
        const groupNamesSet = new Set();

        for (const day of scheduleData) {
            if (!day.groups) continue;
            for (const g of day.groups) {
                if (!g.group) continue;
                groupRegex.lastIndex = 0;
                let m;
                while ((m = groupRegex.exec(g.group)) !== null) {
                    groupNamesSet.add(m[1].trim());
                }
            }
        }

        if (groupNamesSet.size === 0) return scheduleData;

        // 2. Батч-запрос групп из базы
        const groupNames = Array.from(groupNamesSet);
        const groups = await Group.find({ name: { $in: groupNames } }).lean();
        const groupNameToId = new Map();
        const groupIds = [];
        for (const grp of groups) {
            groupNameToId.set(grp.name.toLowerCase(), grp.id);
            groupIds.push(grp.id);
        }

        if (groupIds.length === 0) return scheduleData;

        // 3. Батч-запрос расписаний групп
        const schedules = await Schedule.find({ groupId: { $in: groupIds } }).lean();
        const groupIdToSchedule = new Map();
        for (const s of schedules) {
            groupIdToSchedule.set(s.groupId, s);
        }

        // 3.1. Обновление устаревших или отсутствующих расписаний групп (старше 7 дней или отсутствуют в БД)
        const ONE_WEEK_MS = 7 * 24 * 60 * 60 * 1000;
        const staleOrMissingGroups = groups.filter(grp => {
            const s = groupIdToSchedule.get(grp.id);
            if (!s || !s.updatedAt) return true;
            return (Date.now() - new Date(s.updatedAt).getTime()) > ONE_WEEK_MS;
        });

        if (staleOrMissingGroups.length > 0) {
            await Promise.all(staleOrMissingGroups.map(async (grp) => {
                try {
                    const liveData = await BackendScheduleService.get_schedule_by_groupId(grp.id, grp.language || 'рус');
                    if (liveData && Array.isArray(liveData) && liveData.length > 0) {
                        const updatedDoc = await Schedule.findOneAndUpdate(
                            { groupId: grp.id },
                            { data: liveData, language: grp.language || 'рус' },
                            { upsert: true, new: true }
                        ).lean();
                        groupIdToSchedule.set(grp.id, updatedDoc || { groupId: grp.id, data: liveData });
                    }
                } catch (fetchErr) {
                    log.warn(`[TeacherScheduleEnricher] Не удалось обновить расписание группы ${grp.name} (${grp.id}): ${fetchErr.message}`);
                }
            }));
        }

        // 4. Обогащение пар расписания преподавателя
        for (let dayIdx = 0; dayIdx < scheduleData.length; dayIdx++) {
            const day = scheduleData[dayIdx];
            if (!day.groups) continue;

            for (const g of day.groups) {
                if (!g.group) continue;

                // Если предмет уже заполнен, пропускаем
                if (g.subject) continue;

                groupRegex.lastIndex = 0;
                let match;
                const subjectsFound = [];

                while ((match = groupRegex.exec(g.group)) !== null) {
                    const grpName = match[1].trim();
                    const grpId = groupNameToId.get(grpName.toLowerCase());
                    if (!grpId) continue;

                    const grpSchedule = groupIdToSchedule.get(grpId);
                    if (!grpSchedule || !grpSchedule.data || !grpSchedule.data[dayIdx]) continue;

                    const daySubjects = grpSchedule.data[dayIdx].subjects || [];
                    const slotTime = normalizeTime(g.time);

                    const slot = daySubjects.find(s => normalizeTime(s.time) === slotTime);
                    if (!slot || !slot.subject) continue;

                    const lines = slot.subject.split('\n').map(l => l.trim()).filter(Boolean);
                    if (lines.length === 0) continue;

                    // Найти строку с фамилией преподавателя
                    let targetLine = lines[0];
                    if (surnameToken) {
                        const normalizedSurname = normalizeHomoglyphs(surnameToken.toLowerCase());
                        const matchedLine = lines.find(l => normalizeHomoglyphs(l.toLowerCase()).includes(normalizedSurname));
                        if (matchedLine) targetLine = matchedLine;
                    }

                    const parsed = parseSubjectLine(targetLine);
                    if (parsed && parsed.subject && parsed.subject !== '-') {
                        subjectsFound.push(parsed);
                    }
                }

                if (subjectsFound.length > 0) {
                    // Дедупликация (для поточных пар)
                    const uniqueSubjects = [];
                    const seen = new Set();
                    for (const item of subjectsFound) {
                        const key = `${item.subject}|${item.lessonType}`;
                        if (!seen.has(key)) {
                            seen.add(key);
                            uniqueSubjects.push(item);
                        }
                    }
                    g.subject = uniqueSubjects.map(s => s.subject).join(' / ');
                    g.lessonType = uniqueSubjects.map(s => s.lessonType).filter(Boolean).join(' / ');
                }
            }
        }
    } catch (e) {
        log.warn(`[TeacherScheduleEnricher] Ошибка при обогащении расписания: ${e.message}`);
    }

    return scheduleData;
}

export default enrichTeacherSchedule;
