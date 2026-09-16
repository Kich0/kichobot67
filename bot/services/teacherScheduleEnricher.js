import { Group } from "../models/group.js";
import { Schedule } from "../models/schedule.js";
import log from "../logging/logging.js";

/**
 * Нормализовать строку времени (убрать ведущий ноль, например: "08.30-09.20" -> "8.30-9.20")
 */
function normalizeTime(timeStr) {
    if (!timeStr) return '';
    return timeStr.trim().replace(/^0/, '');
}

/**
 * Извлечь фамилию преподавателя для сопоставления в ячейках расписания группы
 */
function extractSurnameToken(fullName) {
    if (!fullName) return '';
    // Слова из букв длиной от 4 символов (с учётом казахских букв)
    const words = fullName.split(/\s+/).map(w => w.replace(/[^А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІіA-Za-z]/g, ''));
    // Игнорируем академические звания и должности
    const ignoreTokens = new Set([
        'ст', 'пр', 'преп', 'доц', 'проф', 'аcсоц', 'ассис', 'ассоц',
        'phd', 'магистр', 'бакалавр', 'доцент', 'профессор', 'преподаватель'
    ]);
    const candidate = words.find(w => w.length >= 4 && !ignoreTokens.has(w.toLowerCase()));
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
                        const matchedLine = lines.find(l => l.toLowerCase().includes(surnameToken.toLowerCase()));
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
