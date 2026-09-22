import { Resvg } from "@resvg/resvg-js";
import log from "../logging/logging.js";

function escapeXml(unsafe) {
    if (!unsafe) return '';
    return unsafe.toString()
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&apos;');
}

function parseGroupSlot(rawGroup) {
    if (!rawGroup) return { groups: [], rooms: [], groupStr: '', roomStr: '' };
    const regex = /([^\s(),]+)\s*(?:\(([^)]+)\))?/g;
    let m;
    const groups = [];
    const rooms = [];
    while ((m = regex.exec(rawGroup)) !== null) {
        if (m[1]) {
            const cleanG = m[1].replace(/,/g, '').trim();
            if (cleanG) groups.push(cleanG);
        }
        if (m[2]) rooms.push(m[2].trim());
    }
    const uniqueRooms = Array.from(new Set(rooms));
    return {
        groups,
        rooms: uniqueRooms,
        groupStr: groups.join(', '),
        roomStr: uniqueRooms.join(', ')
    };
}

function splitSubjectText(text, maxLine = 22) {
    if (!text) return [];
    const words = text.trim().split(/\s+/);
    if (words.length === 1) {
        if (words[0].length > maxLine) {
            return [words[0].substring(0, maxLine - 1) + '…'];
        }
        return [words[0]];
    }
    const lines = [];
    let curLine = '';
    for (let i = 0; i < words.length; i++) {
        const w = words[i];
        if (!curLine) {
            curLine = w;
        } else if ((curLine + ' ' + w).length <= maxLine) {
            curLine += ' ' + w;
        } else {
            lines.push(curLine);
            curLine = w;
            if (lines.length >= 2) {
                if (i < words.length - 1) {
                    lines[1] = lines[1].substring(0, maxLine - 2) + '…';
                }
                break;
            }
        }
    }
    if (curLine && lines.length < 2) lines.push(curLine);
    return lines;
}

function normalizeTime(t) {
    if (!t) return '';
    return t
        .replace(/[:]/g, '.')
        .replace(/[–—]/g, '-')
        .replace(/(^|-)0(\d)/g, '$1$2')
        .trim();
}

function formatLessonTypeBadge(type) {
    if (!type) return '';
    const lower = type.toLowerCase().trim();
    if (lower.includes('лекц') || lower === 'лек') return '(Лекция)';
    if (lower.includes('прак') || lower.includes('семин') || lower === 'пр') return '(Прак.зан.)';
    if (lower.includes('лаб')) return '(Лаб.раб.)';
    if (lower.includes('сроп')) return '(СРОП)';
    return `(${type.trim()})`;
}

/**
 * Разбивает массив групп на строки так, чтобы в каждой строке помещалось не больше maxChars
 */
function wrapGroupsToLines(groups, maxChars = 20) {
    if (!groups || groups.length === 0) return [];
    const lines = [];
    let currentLine = '';

    for (const g of groups) {
        if (!currentLine) {
            currentLine = g;
        } else if ((currentLine + ', ' + g).length <= maxChars) {
            currentLine += ', ' + g;
        } else {
            lines.push(currentLine);
            currentLine = g;
        }
    }
    if (currentLine) lines.push(currentLine);
    return lines;
}

/**
 * Рассчитывает структуру строк и высоту содержимого для ячейки пары
 */
function layoutCellContent(slot) {
    if (!slot || !slot.group || !slot.group.trim()) {
        return null;
    }

    const parsed = parseGroupSlot(slot.group);
    const groupsList = (slot.groupsList && slot.groupsList.length > 0) ? slot.groupsList : parsed.groups;
    const roomStr = parsed.roomStr || (slot.room ? `${slot.room}${slot.building ? '/' + slot.building : ''}` : '');
    const subjectLines = slot.subject ? splitSubjectText(slot.subject, 21) : [];
    const lessonType = slot.lessonType ? formatLessonTypeBadge(slot.lessonType) : '';

    const groupLines = wrapGroupsToLines(groupsList, 20);

    let lineCount = groupLines.length;
    if (roomStr) lineCount += 1;
    lineCount += subjectLines.length;
    if (lessonType) lineCount += 1;

    // Базовая комфортная высота строки 16px + внутренние паддинги
    const estimatedHeight = Math.max(90, 20 + lineCount * 16);

    return {
        groupsList,
        groupLines,
        roomStr,
        subjectLines,
        lessonType,
        lineCount,
        estimatedHeight
    };
}

class TeacherTableImageService {
    constructor() {
        // LRU Cache: Map preserves insertion order
        // Key: teacherId_lang (String)
        // Value: { buffer: Buffer, expiresAt: number }
        this.cache = new Map();
        this.MAX_ENTRIES = 200; // ~28-32 MB максимум в RAM
        this.TTL = 20 * 60 * 1000; // 20 минут (согласно настройке таблицы недели)
    }

    /**
     * Построение адаптивного SVG с динамическим вертикальным растягиванием строк
     */
    buildSvg(teacher, scheduleData, lang = 'ru') {
        const colors = {
            canvasBg: '#ffffff',
            border: '#cbd5e1',
            titleText: '#0f172a',
            headerBg: '#475569',
            headerText: '#ffffff',
            headerSub: '#f1f5f9',
            dayBg: '#e2e8f0',
            dayText: '#1e293b',
            busyBg: '#15803d',       // насыщенный благородный темно-зеленый
            groupText: '#ffffff',
            roomText: '#fef08a',      // яркий желтый акцент для аудитории
            subjectText: '#e2e8f0',   // контрастный светлый для названия предмета
            lessonTypeText: '#86efac',// мягкий мятно-зеленый для типа пары
            freeBg: '#b91c1c',        // приглушенный темно-красный
            dashColor: '#f8fafc'
        };

        const standardTimes = [
            "8.30-9.20", "9.30-10.20", "10.40-11.30", "11.40-12.30",
            "12.40-13.30", "13.40-14.30", "14.40-15.30", "15.50-16.40",
            "16.50-17.40", "17.50-18.40"
        ];

        const daysRu = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
        const daysKz = ['Дүйсенбі', 'Сейсенбі', 'Сәрсенбі', 'Бейсенбі', 'Жұма', 'Сенбі'];
        const defaultDays = (lang === 'kz' ? daysKz : daysRu).map(day => ({ day, groups: [] }));

        if (!Array.isArray(scheduleData) || scheduleData.length === 0) {
            scheduleData = defaultDays;
        }

        const activeTimes = standardTimes.filter(t => {
            const normT = normalizeTime(t);
            return scheduleData.some(d => d.groups?.some(g => normalizeTime(g.time) === normT && g.group && g.group.trim()));
        });
        const times = activeTimes.length > 0 ? activeTimes : standardTimes.slice(0, 8);

        const padX = 16;
        const padY = 16;
        const titleHeight = 52;
        const colHeaderHeight = 44;
        const dayColWidth = 105;
        const colWidth = 158;
        const cellGap = 4;
        const minRowHeight = 90;

        // 1. ДИНАМИЧЕСКИЙ РАСЧЕТ ВЫСОТЫ КАЖДОЙ СТРОКИ (ДНЯ НЕДЕЛИ)
        const rowLayouts = [];
        const rowHeights = [];

        scheduleData.forEach(day => {
            const cellLayouts = [];
            let maxRowH = minRowHeight;

            times.forEach(t => {
                const normT = normalizeTime(t);
                const slot = day.groups?.find(g => normalizeTime(g.time) === normT);
                const layout = layoutCellContent(slot);
                cellLayouts.push(layout);
                if (layout && layout.estimatedHeight > maxRowH) {
                    maxRowH = layout.estimatedHeight;
                }
            });

            rowLayouts.push(cellLayouts);
            rowHeights.push(maxRowH);
        });

        const numCols = times.length;
        const width = padX * 2 + dayColWidth + numCols * (colWidth + cellGap) - cellGap;
        const totalRowsHeight = rowHeights.reduce((sum, h) => sum + h, 0) + (rowHeights.length - 1) * cellGap;
        const height = padY * 2 + titleHeight + colHeaderHeight + cellGap + totalRowsHeight;

        const startTableY = padY + titleHeight;
        const tableElements = [];

        // Шапка "День \ Время"
        const dayTimeLabel = lang === 'kz' ? 'Күн \\ Уақыт' : 'День \\ Время';
        tableElements.push(`
            <rect x="${padX}" y="${startTableY}" width="${dayColWidth}" height="${colHeaderHeight}" rx="6" fill="${colors.headerBg}" />
            <text x="${padX + dayColWidth / 2}" y="${startTableY + 27}" fill="${colors.headerText}" font-size="12" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${dayTimeLabel}</text>
        `);

        // Шапка колонок времени пар
        const pairWord = lang === 'kz' ? 'сабақ' : 'пара';
        times.forEach((t, i) => {
            const x = padX + dayColWidth + cellGap + i * (colWidth + cellGap);
            tableElements.push(`
                <rect x="${x}" y="${startTableY}" width="${colWidth}" height="${colHeaderHeight}" rx="6" fill="${colors.headerBg}" />
                <text x="${x + colWidth / 2}" y="${startTableY + 18}" fill="${colors.headerSub}" font-size="11.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${i + 1} ${pairWord}</text>
                <text x="${x + colWidth / 2}" y="${startTableY + 33}" fill="${colors.headerText}" font-size="10.5" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${t}</text>
            `);
        });

        // Строки расписания
        let currentY = startTableY + colHeaderHeight + cellGap;

        scheduleData.forEach((day, rIdx) => {
            const rowH = rowHeights[rIdx];
            const cellLayouts = rowLayouts[rIdx];

            // Ячейка названия дня недели (вертикально центрирована)
            tableElements.push(`
                <rect x="${padX}" y="${currentY}" width="${dayColWidth}" height="${rowH}" rx="6" fill="${colors.dayBg}" />
                <text x="${padX + dayColWidth / 2}" y="${currentY + rowH / 2 + 5}" fill="${colors.dayText}" font-size="13" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(day.day)}</text>
            `);

            // Ячейки времени
            times.forEach((t, cIdx) => {
                const x = padX + dayColWidth + cellGap + cIdx * (colWidth + cellGap);
                const layout = cellLayouts[cIdx];
                const clipId = `clip-${rIdx}-${cIdx}`;

                if (layout) {
                    // Занятая пара
                    const textElements = [];
                    // Вертикальное центрирование текстового блока внутри ячейки
                    const contentLinesCount = layout.groupLines.length 
                        + (layout.roomStr ? 1 : 0) 
                        + layout.subjectLines.length 
                        + (layout.lessonType ? 1 : 0);
                    
                    const lineHeight = 15.5;
                    const totalTextBlockHeight = (contentLinesCount - 1) * lineHeight;
                    let curLineY = currentY + (rowH - totalTextBlockHeight) / 2 + 3;

                    // 1. Группы
                    for (const gLine of layout.groupLines) {
                        const fSize = layout.groupLines.length > 2 ? 10 : 11;
                        textElements.push(`
                            <text x="${x + colWidth / 2}" y="${curLineY.toFixed(1)}" fill="${colors.groupText}" font-size="${fSize}" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(gLine)}</text>
                        `);
                        curLineY += lineHeight;
                    }

                    // 2. Аудитория
                    if (layout.roomStr) {
                        textElements.push(`
                            <text x="${x + colWidth / 2}" y="${curLineY.toFixed(1)}" fill="${colors.roomText}" font-size="10.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">Ауд. ${escapeXml(layout.roomStr)}</text>
                        `);
                        curLineY += lineHeight;
                    }

                    // 3. Предмет
                    for (const sLine of layout.subjectLines) {
                        textElements.push(`
                            <text x="${x + colWidth / 2}" y="${curLineY.toFixed(1)}" fill="${colors.subjectText}" font-size="10.5" font-weight="600" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(sLine)}</text>
                        `);
                        curLineY += lineHeight;
                    }

                    // 4. Тип занятия
                    if (layout.lessonType) {
                        textElements.push(`
                            <text x="${x + colWidth / 2}" y="${curLineY.toFixed(1)}" fill="${colors.lessonTypeText}" font-size="10" font-weight="500" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(layout.lessonType)}</text>
                        `);
                    }

                    tableElements.push(`
                        <clipPath id="${clipId}">
                            <rect x="${x}" y="${currentY}" width="${colWidth}" height="${rowH}" rx="6" />
                        </clipPath>
                        <rect x="${x}" y="${currentY}" width="${colWidth}" height="${rowH}" rx="6" fill="${colors.busyBg}" />
                        <g clip-path="url(#${clipId})">
                            ${textElements.join('\n')}
                        </g>
                    `);
                } else {
                    // Свободное окно
                    tableElements.push(`
                        <rect x="${x}" y="${currentY}" width="${colWidth}" height="${rowH}" rx="6" fill="${colors.freeBg}" />
                        <text x="${x + colWidth / 2}" y="${currentY + rowH / 2 + 7}" fill="${colors.dashColor}" font-size="22" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">-</text>
                    `);
                }
            });

            currentY += rowH + cellGap;
        });

        const rawName = typeof teacher === 'string' ? teacher : (teacher?.name || 'Преподаватель');
        const cleanName = rawName.replace(/^преп\.\s*/i, '');
        const titlePrefix = lang === 'kz' ? 'Оқытушының жүктемесі' : 'Загруженность преп.';
        const title = `${titlePrefix} ${cleanName}`;

        return `
<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
    <rect width="${width}" height="${height}" rx="10" fill="${colors.canvasBg}" stroke="${colors.border}" stroke-width="1" />
    <text x="${width / 2}" y="${padY + 30}" fill="${colors.titleText}" font-size="20" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(title)}</text>
    ${tableElements.join('\n')}
</svg>
        `.trim();
    }

    /**
     * Получить PNG-буфер таблицы расписания (с кэшированием в памяти до 30 МБ)
     * @param {Object|String} teacher
     * @param {Array} scheduleData
     * @param {String} lang
     * @returns {Promise<Buffer>}
     */
    async getTeacherTableImage(teacher, scheduleData, lang = 'ru') {
        const teacherId = typeof teacher === 'object' ? (teacher?.id || teacher?._id || teacher?.name || 'unknown') : String(teacher);
        const cacheKey = `${teacherId}_${lang}`;
        const now = Date.now();

        // 1. Проверяем кэш
        if (this.cache.has(cacheKey)) {
            const entry = this.cache.get(cacheKey);
            if (entry.expiresAt > now) {
                // Обновляем позицию для LRU (удаляем и вставляем в конец)
                this.cache.delete(cacheKey);
                this.cache.set(cacheKey, entry);
                return entry.buffer;
            } else {
                this.cache.delete(cacheKey);
            }
        }

        // 2. Генерируем SVG с адаптивными высотами строк
        const svg = this.buildSvg(teacher, scheduleData, lang);

        // 3. Рендерим PNG через @resvg/resvg-js в Full HD (1920px) с аппаратным сглаживанием
        const resvg = new Resvg(svg, {
            fitTo: { mode: 'width', value: 1920 },
            background: '#ffffff',
            textRendering: 1, // optimizeLegibility (включает кернинг и резкость глифов)
            shapeRendering: 2  // geometricPrecision (сглаженные края блоков)
        });
        const pngBuffer = resvg.render().asPng();

        // 4. Проверяем лимит LRU кэша (максимум 200 записей ~ 30 МБ)
        if (this.cache.size >= this.MAX_ENTRIES) {
            const oldestKey = this.cache.keys().next().value;
            this.cache.delete(oldestKey);
        }

        // 5. Сохраняем в кэш
        this.cache.set(cacheKey, {
            buffer: pngBuffer,
            expiresAt: now + this.TTL
        });

        return pngBuffer;
    }

    /**
     * Сбросить кэш картинки конкретного преподавателя
     * @param {String|Number} teacherId
     */
    invalidateTeacherImage(teacherId) {
        if (!teacherId) return;
        this.cache.delete(`${teacherId}_ru`);
        this.cache.delete(`${teacherId}_kz`);
    }
}

export default new TeacherTableImageService();
