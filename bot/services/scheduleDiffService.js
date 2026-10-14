/**
 * Сервис сравнения версий расписаний (Schedule Diff Engine).
 *
 * Выявляет любые точечные изменения между двумя версиями расписания:
 * - ➕ Добавление пар
 * - ➖ Удаление / отмена пар
 * - ✏️ Изменение содержимого (с точностью до буквы/цифры/аудитории/преподавателя)
 * - 🔄 Перенос пар по времени или дням
 */

/**
 * Находит общее начало и конец двух строк и возвращает компактное описание различия.
 * Например: "Ауд.305/1" и "Ауд.307/1" -> { prefix: "Ауд.30", oldPart: "5", newPart: "7", suffix: "/1", diffText: "[305/1 ➔ 307/1]" }
 */
export function findInlineDiff(oldStr, newStr) {
    const s1 = String(oldStr || "").trim();
    const s2 = String(newStr || "").trim();
    if (s1 === s2) return null;

    // Построчное сравнение
    const lines1 = s1.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const lines2 = s2.split(/\r?\n/).map(l => l.trim()).filter(Boolean);

    const changedLines = [];
    const maxLines = Math.max(lines1.length, lines2.length);

    for (let i = 0; i < maxLines; i++) {
        const l1 = lines1[i] || "";
        const l2 = lines2[i] || "";
        if (l1 === l2) continue;

        if (!l1 && l2) {
            changedLines.push(`➕ добавлено: "${l2}"`);
            continue;
        }
        if (l1 && !l2) {
            changedLines.push(`➖ удалено: "${l1}"`);
            continue;
        }

        // Обе строки есть, но различаются: находим пословный/посимвольный diff
        let start = 0;
        while (start < l1.length && start < l2.length && l1[start] === l2[start]) {
            start++;
        }
        let end1 = l1.length - 1;
        let end2 = l2.length - 1;
        while (end1 >= start && end2 >= start && l1[end1] === l2[end2]) {
            end1--;
            end2--;
        }

        const diffOld = l1.substring(start, end1 + 1);
        const diffNew = l2.substring(start, end2 + 1);

        // Находим границы слов вокруг изменения для контекста
        let wStart1 = start;
        while (wStart1 > 0 && !/\s/.test(l1[wStart1 - 1])) wStart1--;
        let wEnd1 = end1;
        while (wEnd1 < l1.length - 1 && !/\s/.test(l1[wEnd1 + 1])) wEnd1++;

        let wStart2 = start;
        while (wStart2 > 0 && !/\s/.test(l2[wStart2 - 1])) wStart2--;
        let wEnd2 = end2;
        while (wEnd2 < l2.length - 1 && !/\s/.test(l2[wEnd2 + 1])) wEnd2++;

        const wordOld = l1.substring(wStart1, wEnd1 + 1).replace(/^[\(\[\{]/, '').replace(/[\)\]\}]$/, '');
        const wordNew = l2.substring(wStart2, wEnd2 + 1).replace(/^[\(\[\{]/, '').replace(/[\)\]\}]$/, '');

        // Определяем характер изменения (аудитория, тип, имя)
        let note = "";
        const lowerL1 = l1.toLowerCase();
        const lowerL2 = l2.toLowerCase();
        if (lowerL1.includes("ауд") || lowerL2.includes("ауд")) {
            note = "аудитория";
        } else if (lowerL1.includes("лекц") || lowerL2.includes("лекц") || lowerL1.includes("прак") || lowerL2.includes("прак")) {
            note = "тип занятия";
        }

        const notePrefix = note ? ` (${note})` : "";
        if (wordOld && wordNew && (wordOld.length > diffOld.length || wordNew.length > diffNew.length)) {
            changedLines.push(`[${wordOld} ➔ ${wordNew}]${notePrefix}`);
        } else if (diffOld && diffNew) {
            changedLines.push(`[${diffOld} ➔ ${diffNew}]${notePrefix}`);
        } else if (diffNew) {
            changedLines.push(`[+ "${diffNew}"]${notePrefix}`);
        } else if (diffOld) {
            changedLines.push(`[- "${diffOld}"]${notePrefix}`);
        }
    }

    return {
        oldText: s1,
        newText: s2,
        diffDetails: changedLines,
        summary: changedLines.join("; ") || `[изменено]`
    };
}

const DAY_INDEX = {
    'понедельник': 1, 'дүйсенбі': 1, 'monday': 1, 'пн': 1, '1': 1,
    'вторник': 2, 'сейсенбі': 2, 'tuesday': 2, 'вт': 2, '2': 2,
    'среда': 3, 'сәрсенбі': 3, 'wednesday': 3, 'ср': 3, '3': 3,
    'четверг': 4, 'бейсенбі': 4, 'thursday': 4, 'чт': 4, '4': 4,
    'пятница': 5, 'жұма': 5, 'friday': 5, 'пт': 5, '5': 5,
    'суббота': 6, 'сенбі': 6, 'saturday': 6, 'сб': 6, '6': 6,
    'воскресенье': 7, 'жексенбі': 7, 'sunday': 7, 'вс': 7, '7': 7
};

export function getDayIndex(dayStr) {
    if (!dayStr) return 0;
    return DAY_INDEX[String(dayStr).toLowerCase().trim()] || 0;
}

const CANONICAL_DAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];

/**
 * Нормализует предмет для поиска переносов (удаляет аудиторию, тип занятия, лишние пробелы)
 */
function extractCoreSubject(str) {
    if (!str) return "";
    return str
        .replace(/\/.*?\//g, "")
        .replace(/Ауд\.?[^\n]*/gi, "")
        .replace(/📚/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
}

/**
 * Сравнивает два расписания студентов.
 * @param {Array} oldData - расписание вида [{ day: 'Понедельник', subjects: [{ time, subject }] }]
 * @param {Array} newData - расписание вида [{ day: 'Понедельник', subjects: [{ time, subject }] }]
 * @returns {{ hasChanges: boolean, changesCount: number, changes: Array, summary: string }}
 */
export function compareStudentSchedules(oldData = [], newData = []) {
    const oldDayByIndex = new Map();
    for (const d of (Array.isArray(oldData) ? oldData : [])) {
        const idx = getDayIndex(d?.day);
        if (idx) oldDayByIndex.set(idx, { dayName: d.day, subjects: d.subjects || [] });
    }

    const newDayByIndex = new Map();
    for (const d of (Array.isArray(newData) ? newData : [])) {
        const idx = getDayIndex(d?.day);
        if (idx) newDayByIndex.set(idx, { dayName: d.day, subjects: d.subjects || [] });
    }

    const changes = [];

    // Собираем все пары старого расписания для поиска переносов
    const allOldSlots = [];
    for (const [dayIdx, entry] of oldDayByIndex.entries()) {
        const day = entry.dayName || CANONICAL_DAYS[dayIdx - 1];
        for (const s of entry.subjects) {
            allOldSlots.push({ day, dayIdx, time: s.time, subject: s.subject, core: extractCoreSubject(s.subject) });
        }
    }

    const matchedOldSlots = new Set();
    const matchedNewSlots = new Set();

    // 1. Сравнение слотов в один и тот же день (по индексу дня 1..6)
    for (let dayIdx = 1; dayIdx <= 6; dayIdx++) {
        const oldEntry = oldDayByIndex.get(dayIdx);
        const newEntry = newDayByIndex.get(dayIdx);
        const day = newEntry?.dayName || oldEntry?.dayName || CANONICAL_DAYS[dayIdx - 1];

        const oldSubs = oldEntry?.subjects || [];
        const newSubs = newEntry?.subjects || [];

        const oldSlotMap = new Map(oldSubs.map((s, idx) => [s.time || `slot_${idx}`, s.subject]));
        const newSlotMap = new Map(newSubs.map((s, idx) => [s.time || `slot_${idx}`, s.subject]));

        // Проверяем общие слоты времени
        for (const [time, newSub] of newSlotMap.entries()) {
            if (oldSlotMap.has(time)) {
                const oldSub = oldSlotMap.get(time);
                matchedOldSlots.add(`${day}_${time}`);
                matchedNewSlots.add(`${day}_${time}`);

                if (String(oldSub).trim() !== String(newSub).trim()) {
                    const diff = findInlineDiff(oldSub, newSub);
                    changes.push({
                        day,
                        time,
                        type: "MODIFIED",
                        typeLabel: "✏️ Изменение",
                        oldValue: oldSub,
                        newValue: newSub,
                        diffText: diff ? diff.summary : "Текст изменён",
                        explanation: `В слоте ${time} изменено: ${diff ? diff.summary : 'содержимое пары'}`
                    });
                }
            }
        }

        // Проверяем слоты, которых не было в старом расписании в этот день
        for (const [time, newSub] of newSlotMap.entries()) {
            const key = `${day}_${time}`;
            if (!matchedNewSlots.has(key)) {
                // Проверяем: не перенос ли это из другого дня/времени?
                const newCore = extractCoreSubject(newSub);
                const movedFrom = newCore
                    ? allOldSlots.find(o => !matchedOldSlots.has(`${o.day}_${o.time}`) && o.core === newCore && (o.day !== day || o.time !== time))
                    : null;

                if (movedFrom) {
                    matchedOldSlots.add(`${movedFrom.day}_${movedFrom.time}`);
                    matchedNewSlots.add(key);
                    changes.push({
                        day,
                        time,
                        type: "MOVED",
                        typeLabel: "🔄 Перенос",
                        oldValue: `${movedFrom.day} ${movedFrom.time}: ${movedFrom.subject}`,
                        newValue: `${day} ${time}: ${newSub}`,
                        diffText: `Перенесено с ${movedFrom.day} (${movedFrom.time}) на ${day} (${time})`,
                        explanation: `Пара перенесена с ${movedFrom.day} ${movedFrom.time} на ${day} ${time}`
                    });
                } else {
                    matchedNewSlots.add(key);
                    changes.push({
                        day,
                        time,
                        type: "ADDED",
                        typeLabel: "➕ Добавлена пара",
                        oldValue: "",
                        newValue: newSub,
                        diffText: `Добавлена пара: ${newSub.replace(/\s+/g, ' ').trim()}`,
                        explanation: `Новая пара в ${time}: ${newSub.replace(/\s+/g, ' ').trim()}`
                    });
                }
            }
        }

        // Проверяем удалённые слоты из старого расписания
        for (const [time, oldSub] of oldSlotMap.entries()) {
            const key = `${day}_${time}`;
            if (!matchedOldSlots.has(key)) {
                matchedOldSlots.add(key);
                changes.push({
                    day,
                    time,
                    type: "REMOVED",
                    typeLabel: "➖ Удалена пара",
                    oldValue: oldSub,
                    newValue: "",
                    diffText: `Снята пара: ${oldSub.replace(/\s+/g, ' ').trim()}`,
                    explanation: `Пара в ${time} снята/отменена`
                });
            }
        }
    }

    const summaries = changes.map(c => `${c.day} ${c.time}: ${c.diffText}`);
    const summary = summaries.join("; ") || (changes.length > 0 ? "Расписание обновлено" : "Без изменений");

    return {
        hasChanges: changes.length > 0,
        changesCount: changes.length,
        changes,
        summary
    };
}

/**
 * Сравнивает два расписания преподавателей.
 * @param {Array} oldData - расписание вида [{ day: 'Понедельник', groups: [{ time, group, subject, lessonType }] }]
 * @param {Array} newData - расписание вида [{ day: 'Понедельник', groups: [{ time, group, subject, lessonType }] }]
 * @returns {{ hasChanges: boolean, changesCount: number, changes: Array, summary: string }}
 */
export function compareTeacherSchedules(oldData = [], newData = []) {
    const oldDayByIndex = new Map();
    for (const d of (Array.isArray(oldData) ? oldData : [])) {
        const idx = getDayIndex(d?.day);
        if (idx) oldDayByIndex.set(idx, { dayName: d.day, groups: d.groups || [] });
    }

    const newDayByIndex = new Map();
    for (const d of (Array.isArray(newData) ? newData : [])) {
        const idx = getDayIndex(d?.day);
        if (idx) newDayByIndex.set(idx, { dayName: d.day, groups: d.groups || [] });
    }

    const changes = [];

    for (let dayIdx = 1; dayIdx <= 6; dayIdx++) {
        const oldEntry = oldDayByIndex.get(dayIdx);
        const newEntry = newDayByIndex.get(dayIdx);
        const day = newEntry?.dayName || oldEntry?.dayName || CANONICAL_DAYS[dayIdx - 1];

        const oldGrps = oldEntry?.groups || [];
        const newGrps = newEntry?.groups || [];

        const oldSlotMap = new Map(oldGrps.map((g, idx) => [g.time || `slot_${idx}`, g]));
        const newSlotMap = new Map(newGrps.map((g, idx) => [g.time || `slot_${idx}`, g]));

        // Проверяем совпадающие по времени занятия
        for (const [time, newG] of newSlotMap.entries()) {
            if (oldSlotMap.has(time)) {
                const oldG = oldSlotMap.get(time);
                const subOld = String(oldG.subject || "").trim();
                const subNew = String(newG.subject || "").trim();
                const grpOld = String(oldG.group || "").trim();
                const grpNew = String(newG.group || "").trim();
                const typeOld = String(oldG.lessonType || "").trim();
                const typeNew = String(newG.lessonType || "").trim();

                const isIdentical = subOld === subNew && grpOld === grpNew && typeOld === typeNew;
                if (!isIdentical) {
                    const diffParts = [];
                    if (subOld !== subNew) {
                        const d = findInlineDiff(subOld, subNew);
                        diffParts.push(`предмет: ${d ? d.summary : `${subOld} ➔ ${subNew}`}`);
                    }
                    if (grpOld !== grpNew) {
                        const d = findInlineDiff(grpOld, grpNew);
                        diffParts.push(`группа/ауд: ${d ? d.summary : `${grpOld} ➔ ${grpNew}`}`);
                    }
                    if (typeOld !== typeNew) {
                        diffParts.push(`тип: [${typeOld || '-'} ➔ ${typeNew || '-'}]`);
                    }

                    const diffSummary = diffParts.join(", ");
                    changes.push({
                        day,
                        time,
                        type: "MODIFIED",
                        typeLabel: "✏️ Изменение",
                        oldValue: `[${grpOld}] ${subOld} (${typeOld})`,
                        newValue: `[${grpNew}] ${subNew} (${typeNew})`,
                        diffText: diffSummary,
                        explanation: `В слоте ${time}: ${diffSummary}`
                    });
                }
            } else {
                changes.push({
                    day,
                    time,
                    type: "ADDED",
                    typeLabel: "➕ Добавлена пара",
                    oldValue: "",
                    newValue: `[${newG.group || '-'}] ${newG.subject || '-'} (${newG.lessonType || '-'})`,
                    diffText: `Добавлена пара: [${newG.group || '-'}] ${newG.subject || '-'}`,
                    explanation: `Новая пара в ${time}: [${newG.group || '-'}] ${newG.subject || '-'}`
                });
            }
        }

        // Проверяем снятые занятия
        for (const [time, oldG] of oldSlotMap.entries()) {
            if (!newSlotMap.has(time)) {
                changes.push({
                    day,
                    time,
                    type: "REMOVED",
                    typeLabel: "➖ Снято занятие",
                    oldValue: `[${oldG.group || '-'}] ${oldG.subject || '-'} (${oldG.lessonType || '-'})`,
                    newValue: "",
                    diffText: `Снято занятие: [${oldG.group || '-'}] ${oldG.subject || '-'}`,
                    explanation: `Занятие в ${time} снято`
                });
            }
        }
    }

    const summaries = changes.map(c => `${c.day} ${c.time}: ${c.diffText}`);
    const summary = summaries.join("; ") || (changes.length > 0 ? "Расписание обновлено" : "Без изменений");

    return {
        hasChanges: changes.length > 0,
        changesCount: changes.length,
        changes,
        summary
    };
}

/**
 * Формирует подробный текстовый блок различий для вставки в отчеты и памятки.
 */
export function formatDetailedDiffText(changes = []) {
    if (!Array.isArray(changes) || changes.length === 0) {
        return "  (Изменений в расписании не обнаружено)\r\n";
    }

    let out = "";
    for (const c of changes) {
        out += `  📅 ${c.day} | ⏰ ${c.time} | ${c.typeLabel}:\r\n`;
        if (c.type === "MODIFIED") {
            out += `     ❌ Было:  ${c.oldValue.replace(/\r?\n/g, ' / ')}\r\n`;
            out += `     ✅ Стало: ${c.newValue.replace(/\r?\n/g, ' / ')}\r\n`;
            out += `     🔍 Точное изменение: ${c.diffText}\r\n`;
        } else if (c.type === "MOVED") {
            out += `     🔄 ${c.diffText}\r\n`;
        } else if (c.type === "ADDED") {
            out += `     ➕ ${c.newValue.replace(/\r?\n/g, ' / ')}\r\n`;
        } else if (c.type === "REMOVED") {
            out += `     ➖ ${c.oldValue.replace(/\r?\n/g, ' / ')}\r\n`;
        }
        out += `\r\n`;
    }
    return out;
}

/**
 * Генерирует персональное досье для группы: актуальное расписание + полная история изменений с диффами.
 */
export function generateGroupDiffDossier({
    groupName,
    groupId,
    faculty,
    activeSchedule,
    versions = []
}) {
    let text = `==========================================================\r\n`;
    text += `📚 РАСПИСАНИЕ И ИСТОРИЯ ИЗМЕНЕНИЙ ГРУППЫ: ${groupName} (ID: ${groupId})\r\n`;
    text += `Факультет: ${faculty || '-'}\r\n`;
    text += `Всего зафиксировано архивных редакций: ${versions.length}\r\n`;
    text += `==========================================================\r\n\r\n`;

    text += `----------------------------------------------------------\r\n`;
    text += `⭐ ТЕКУЩЕЕ АКТУАЛЬНОЕ РАСПИСАНИЕ (ПРЯМО СЕЙЧАС):\r\n`;
    text += `----------------------------------------------------------\r\n`;

    if (Array.isArray(activeSchedule?.data) && activeSchedule.data.length > 0) {
        for (const d of activeSchedule.data) {
            text += `📅 ${d.day || 'День'}:\r\n`;
            if (Array.isArray(d.subjects) && d.subjects.length > 0) {
                for (const s of d.subjects) {
                    text += `  ⏰ ${s.time || '--:--'} | 📖 ${String(s.subject || '-').replace(/\r?\n/g, ' / ')}\r\n`;
                }
            } else {
                text += `  (Занятий нет)\r\n`;
            }
            text += `\r\n`;
        }
    } else {
        text += `  (Расписание пусто)\r\n\r\n`;
    }

    text += `==========================================================\r\n`;
    text += `🔄 ХРОНОЛОГИЯ И ДЕТАЛЬНЫЕ ИЗМЕНЕНИЯ (ЧТО И НА ЧТО МЕНЯЛИ):\r\n`;
    text += `==========================================================\r\n\r\n`;

    if (versions.length === 0) {
        text += `💡 Для этой группы изменений расписания ещё не происходило.\r\n`;
        text += `Действует первоначальная версия расписания.\r\n`;
        return text;
    }

    // Сортируем версии от самых свежих к старым
    const sortedVersions = [...versions].sort((a, b) => new Date(b.archivedAt || b.validTo || 0) - new Date(a.archivedAt || a.validTo || 0));

    sortedVersions.forEach((v, index) => {
        const num = sortedVersions.length - index;
        text += `▶ ПРАВКА #${num} (Архивация: ${v.archivedAtFormatted || v.validToFormatted || 'Неизвестно'}):\r\n`;
        text += `  Период действия прошлой версии: с ${v.validFromFormatted || '-'} по ${v.validToFormatted || '-'}\r\n`;
        text += `  Хэш прошлой версии: ${v.hash || '-'}\r\n`;
        text += `  Кратко: ${v.diffSummary || 'Изменение расписания'}\r\n`;
        text += `  Детализация правок (Было ➔ Стало):\r\n`;

        if (Array.isArray(v.diffDetails) && v.diffDetails.length > 0) {
            text += formatDetailedDiffText(v.diffDetails);
        } else {
            text += `  (Автоматический дифф: ${v.diffSummary || 'зафиксировано изменение хэша'})\r\n\r\n`;
        }
        text += `----------------------------------------------------------\r\n\r\n`;
    });

    return text;
}

/**
 * Генерирует персональное досье для преподавателя: актуальное расписание + полная история изменений с диффами.
 */
export function generateTeacherDiffDossier({
    teacherName,
    teacherId,
    department,
    activeSchedule,
    versions = []
}) {
    let text = `==========================================================\r\n`;
    text += `👨‍🏫 РАСПИСАНИЕ И ИСТОРИЯ ИЗМЕНЕНИЙ ПРЕПОДАВАТЕЛЯ: ${teacherName} (ID: ${teacherId})\r\n`;
    text += `Кафедра: ${department || '-'}\r\n`;
    text += `Всего зафиксировано архивных редакций: ${versions.length}\r\n`;
    text += `==========================================================\r\n\r\n`;

    text += `----------------------------------------------------------\r\n`;
    text += `⭐ ТЕКУЩЕЕ АКТУАЛЬНОЕ РАСПИСАНИЕ (ПРЯМО СЕЙЧАС):\r\n`;
    text += `----------------------------------------------------------\r\n`;

    if (Array.isArray(activeSchedule?.data) && activeSchedule.data.length > 0) {
        for (const d of activeSchedule.data) {
            text += `📅 ${d.day || 'День'}:\r\n`;
            if (Array.isArray(d.groups) && d.groups.length > 0) {
                for (const g of d.groups) {
                    text += `  ⏰ ${g.time || '--:--'} | 👥 [${g.group || '-'}] 📖 ${g.subject || '-'} (${g.lessonType || ''})\r\n`;
                }
            } else {
                text += `  (Занятий нет)\r\n`;
            }
            text += `\r\n`;
        }
    } else {
        text += `  (Расписание пусто)\r\n\r\n`;
    }

    text += `==========================================================\r\n`;
    text += `🔄 ХРОНОЛОГИЯ И ДЕТАЛЬНЫЕ ИЗМЕНЕНИЯ (ЧТО И НА ЧТО МЕНЯЛИ):\r\n`;
    text += `==========================================================\r\n\r\n`;

    if (versions.length === 0) {
        text += `💡 Для этого преподавателя изменений расписания ещё не происходило.\r\n`;
        text += `Действует первоначальная версия расписания.\r\n`;
        return text;
    }

    const sortedVersions = [...versions].sort((a, b) => new Date(b.archivedAt || b.validTo || 0) - new Date(a.archivedAt || a.validTo || 0));

    sortedVersions.forEach((v, index) => {
        const num = sortedVersions.length - index;
        text += `▶ ПРАВКА #${num} (Архивация: ${v.archivedAtFormatted || v.validToFormatted || 'Неизвестно'}):\r\n`;
        text += `  Период действия прошлой версии: с ${v.validFromFormatted || '-'} по ${v.validToFormatted || '-'}\r\n`;
        text += `  Хэш прошлой версии: ${v.hash || '-'}\r\n`;
        text += `  Кратко: ${v.diffSummary || 'Изменение расписания'}\r\n`;
        text += `  Детализация правок (Было ➔ Стало):\r\n`;

        if (Array.isArray(v.diffDetails) && v.diffDetails.length > 0) {
            text += formatDetailedDiffText(v.diffDetails);
        } else {
            text += `  (Автоматический дифф: ${v.diffSummary || 'зафиксировано изменение хэша'})\r\n\r\n`;
        }
        text += `----------------------------------------------------------\r\n\r\n`;
    });

    return text;
}
