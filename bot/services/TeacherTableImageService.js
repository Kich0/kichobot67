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
 * Рассчитывает структуру строк и высоту содержимого для ячейки пары с поддержкой 2 колонок для 6+ групп
 */
function layoutCellContent(slot, colWidth = 200) {
    if (!slot || !slot.group || !slot.group.trim()) {
        return null;
    }

    const parsed = parseGroupSlot(slot.group);
    const groupsList = (slot.groupsList && slot.groupsList.length > 0) ? slot.groupsList : parsed.groups;
    const roomStr = parsed.roomStr || (slot.room ? `${slot.room}${slot.building ? '/' + slot.building : ''}` : '');
    const maxSubjChars = Math.max(18, Math.floor(colWidth / 9.5));
    const subjectLines = slot.subject ? splitSubjectText(slot.subject, maxSubjChars) : [];
    const lessonType = slot.lessonType ? formatLessonTypeBadge(slot.lessonType) : '';

    const isLargeStream = groupsList.length >= 6;
    let groupLines = [];
    let groupColumns = null;

    if (isLargeStream && colWidth >= 200) {
        // Двухколоночная верстка для групп потока (по 3-5 групп в каждой микро-колонке)
        const mid = Math.ceil(groupsList.length / 2);
        const col1 = groupsList.slice(0, mid);
        const col2 = groupsList.slice(mid);
        groupColumns = { col1, col2 };
    } else {
        const maxGroupChars = Math.max(16, Math.floor(colWidth / 9));
        groupLines = wrapGroupsToLines(groupsList, maxGroupChars);
    }

    let lineCount = 0;
    if (groupColumns) {
        lineCount += Math.max(groupColumns.col1.length, groupColumns.col2.length);
    } else {
        lineCount += groupLines.length;
    }
    if (roomStr) lineCount += 1;
    lineCount += subjectLines.length;
    if (lessonType) lineCount += 1;

    // Комфортная динамическая высота строки с запасом для крупных шрифтов
    const estimatedHeight = Math.max(105, 28 + lineCount * 18);

    return {
        groupsList,
        groupLines,
        groupColumns,
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
        this.MAX_ENTRIES = 80; // ~14 MB максимум в RAM (защита от Render 512MB OOM)
        this.TTL = 20 * 60 * 1000; // 20 минут (согласно настройке таблицы недели)

        // Фоновая очистка протухших буферов изображений раз в 10 минут
        setInterval(() => {
            const now = Date.now();
            for (const [k, entry] of this.cache.entries()) {
                if (entry && entry.expiresAt <= now) {
                    this.cache.delete(k);
                }
            }
        }, 10 * 60 * 1000).unref();
    }

    /**
     * Построение адаптивного SVG с аутентичным стилем КарУ:
     * - Умное отсечение пустых крайних пар (Active Slots Window)
     * - Расширенные колонки с увеличенными читаемыми шрифтами
     * - Динамические строки и двухколоночный поток для 6+ групп
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
            roomText: '#fef08a',      // яркий золотисто-желтый акцент для аудитории
            subjectText: '#e2e8f0',   // контрастный светлый для названия предмета
            lessonTypeText: '#86efac',// мягкий мятно-зеленый для типа пары
            freeBg: '#b91c1c',        // приглушенный темно-красный для окон
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

        // 1. ОПРЕДЕЛЕНИЕ ДИАПАЗОНА АКТИВНЫХ ПАР (Active Slots Trimming)
        let minActiveIndex = -1;
        let maxActiveIndex = -1;

        standardTimes.forEach((t, idx) => {
            const normT = normalizeTime(t);
            const hasSlot = scheduleData.some(d => d.groups?.some(g => normalizeTime(g.time) === normT && g.group && g.group.trim()));
            if (hasSlot) {
                if (minActiveIndex === -1) minActiveIndex = idx;
                maxActiveIndex = idx;
            }
        });

        let activeTimes = [];
        let startPairNumber = 1;

        if (minActiveIndex === -1) {
            activeTimes = standardTimes.slice(0, 6);
            startPairNumber = 1;
        } else {
            let start = Math.max(0, minActiveIndex);
            let end = Math.min(standardTimes.length - 1, maxActiveIndex);

            // Обеспечиваем минимум 5 пар для устойчивой сбалансированной сетки
            if (end - start + 1 < 5) {
                if (start > 0) start = Math.max(0, start - 1);
                if (end < standardTimes.length - 1) end = Math.min(standardTimes.length - 1, end + 1);
            }
            if (end - start + 1 < 5 && end < standardTimes.length - 1) {
                end = Math.min(standardTimes.length - 1, start + 4);
            }

            activeTimes = standardTimes.slice(start, end + 1);
            startPairNumber = start + 1;
        }

        const times = activeTimes;
        const numCols = times.length;

        // 2. АДАПТИВНАЯ ГЕОМЕТРИЯ СЕТКИ
        const padX = 18;
        const padY = 18;
        const titleHeight = 56;
        const colHeaderHeight = 48;
        const dayColWidth = 115;
        const cellGap = 5;
        const minRowHeight = 100;

        // Расчет ширины колонок: просторные колонки с крупным шрифтом
        let colWidth = 220;
        if (numCols <= 5) {
            colWidth = 265;
        } else if (numCols === 6) {
            colWidth = 240;
        } else if (numCols === 7) {
            colWidth = 215;
        } else {
            colWidth = 195;
        }

        const width = padX * 2 + dayColWidth + numCols * (colWidth + cellGap) - cellGap;

        // 3. РАСЧЕТ ДИНАМИЧЕСКИХ ВЫСОТ СТРОК
        const rowLayouts = [];
        const rowHeights = [];

        scheduleData.forEach(day => {
            const cellLayouts = [];
            let maxRowH = minRowHeight;

            times.forEach(t => {
                const normT = normalizeTime(t);
                const slot = day.groups?.find(g => normalizeTime(g.time) === normT);
                const layout = layoutCellContent(slot, colWidth);
                cellLayouts.push(layout);
                if (layout && layout.estimatedHeight > maxRowH) {
                    maxRowH = layout.estimatedHeight;
                }
            });

            rowLayouts.push(cellLayouts);
            rowHeights.push(maxRowH);
        });

        const totalRowsHeight = rowHeights.reduce((sum, h) => sum + h, 0) + (rowHeights.length - 1) * cellGap;
        const height = padY * 2 + titleHeight + colHeaderHeight + cellGap + totalRowsHeight;

        const startTableY = padY + titleHeight;
        const tableElements = [];

        // Шапка "День \ Время"
        const dayTimeLabel = lang === 'kz' ? 'Күн \\ Уақыт' : 'День \\ Время';
        tableElements.push(`
            <rect x="${padX}" y="${startTableY}" width="${dayColWidth}" height="${colHeaderHeight}" rx="7" fill="${colors.headerBg}" />
            <text x="${padX + dayColWidth / 2}" y="${startTableY + 29}" fill="${colors.headerText}" font-size="13.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${dayTimeLabel}</text>
        `);

        // Шапка колонок времени пар
        const pairWord = lang === 'kz' ? 'сабақ' : 'пара';
        times.forEach((t, i) => {
            const pairNum = startPairNumber + i;
            const x = padX + dayColWidth + cellGap + i * (colWidth + cellGap);
            tableElements.push(`
                <rect x="${x}" y="${startTableY}" width="${colWidth}" height="${colHeaderHeight}" rx="7" fill="${colors.headerBg}" />
                <text x="${x + colWidth / 2}" y="${startTableY + 20}" fill="${colors.headerSub}" font-size="13" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${pairNum} ${pairWord}</text>
                <text x="${x + colWidth / 2}" y="${startTableY + 37}" fill="${colors.headerText}" font-size="12" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${t}</text>
            `);
        });

        // Строки дней недели
        let currentY = startTableY + colHeaderHeight + cellGap;

        scheduleData.forEach((day, rIdx) => {
            const rowH = rowHeights[rIdx];
            const cellLayouts = rowLayouts[rIdx];

            // Ячейка названия дня недели (вертикально центрирована)
            tableElements.push(`
                <rect x="${padX}" y="${currentY}" width="${dayColWidth}" height="${rowH}" rx="7" fill="${colors.dayBg}" />
                <text x="${padX + dayColWidth / 2}" y="${currentY + rowH / 2 + 5}" fill="${colors.dayText}" font-size="14.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(day.day)}</text>
            `);

            // Ячейки времени
            times.forEach((t, cIdx) => {
                const x = padX + dayColWidth + cellGap + cIdx * (colWidth + cellGap);
                const layout = cellLayouts[cIdx];
                const clipId = `clip-${rIdx}-${cIdx}`;

                if (layout) {
                    // Занятая пара
                    const textElements = [];

                    if (layout.groupColumns) {
                        // Двухколоночный режим для больших потоков (6-10 групп)
                        const { col1, col2 } = layout.groupColumns;
                        const groupRows = Math.max(col1.length, col2.length);
                        const groupLineH = 15;
                        const groupBlockH = groupRows * groupLineH;

                        const extraLines = (layout.roomStr ? 1 : 0) + layout.subjectLines.length + (layout.lessonType ? 1 : 0);
                        const extraLineH = 17;
                        const totalH = groupBlockH + extraLines * extraLineH + 6;

                        let curY = currentY + (rowH - totalH) / 2 + 12;

                        const col1X = x + colWidth * 0.28;
                        const col2X = x + colWidth * 0.72;

                        for (let gIdx = 0; gIdx < groupRows; gIdx++) {
                            const g1 = col1[gIdx] || '';
                            const g2 = col2[gIdx] || '';
                            if (g1) {
                                textElements.push(`<text x="${col1X.toFixed(1)}" y="${curY.toFixed(1)}" fill="${colors.groupText}" font-size="11.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(g1)}</text>`);
                            }
                            if (g2) {
                                textElements.push(`<text x="${col2X.toFixed(1)}" y="${curY.toFixed(1)}" fill="${colors.groupText}" font-size="11.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(g2)}</text>`);
                            }
                            curY += groupLineH;
                        }

                        curY += 4;

                        // Аудитория
                        if (layout.roomStr) {
                            textElements.push(`<text x="${x + colWidth / 2}" y="${curY.toFixed(1)}" fill="${colors.roomText}" font-size="12.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">Ауд. ${escapeXml(layout.roomStr)}</text>`);
                            curY += extraLineH;
                        }

                        // Предмет
                        for (const sLine of layout.subjectLines) {
                            textElements.push(`<text x="${x + colWidth / 2}" y="${curY.toFixed(1)}" fill="${colors.subjectText}" font-size="12" font-weight="600" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(sLine)}</text>`);
                            curY += extraLineH;
                        }

                        // Тип пары
                        if (layout.lessonType) {
                            textElements.push(`<text x="${x + colWidth / 2}" y="${curY.toFixed(1)}" fill="${colors.lessonTypeText}" font-size="11.5" font-weight="500" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(layout.lessonType)}</text>`);
                        }
                    } else {
                        // Обычный центрированный режим (1-5 групп) с крупным шрифтом
                        const contentLinesCount = layout.groupLines.length 
                            + (layout.roomStr ? 1 : 0) 
                            + layout.subjectLines.length 
                            + (layout.lessonType ? 1 : 0);

                        const lineH = 18;
                        const totalH = (contentLinesCount - 1) * lineH;
                        let curLineY = currentY + (rowH - totalH) / 2 + 4;

                        // 1. Группы (13.5px)
                        for (const gLine of layout.groupLines) {
                            const fSize = layout.groupLines.length > 2 ? 12 : 13.5;
                            textElements.push(`
                                <text x="${x + colWidth / 2}" y="${curLineY.toFixed(1)}" fill="${colors.groupText}" font-size="${fSize}" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(gLine)}</text>
                            `);
                            curLineY += lineH;
                        }

                        // 2. Аудитория (12.5px)
                        if (layout.roomStr) {
                            textElements.push(`
                                <text x="${x + colWidth / 2}" y="${curLineY.toFixed(1)}" fill="${colors.roomText}" font-size="12.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">Ауд. ${escapeXml(layout.roomStr)}</text>
                            `);
                            curLineY += lineH;
                        }

                        // 3. Предмет (12px)
                        for (const sLine of layout.subjectLines) {
                            textElements.push(`
                                <text x="${x + colWidth / 2}" y="${curLineY.toFixed(1)}" fill="${colors.subjectText}" font-size="12" font-weight="600" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(sLine)}</text>
                            `);
                            curLineY += lineH;
                        }

                        // 4. Тип занятия (11.5px)
                        if (layout.lessonType) {
                            textElements.push(`
                                <text x="${x + colWidth / 2}" y="${curLineY.toFixed(1)}" fill="${colors.lessonTypeText}" font-size="11.5" font-weight="500" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(layout.lessonType)}</text>
                            `);
                        }
                    }

                    tableElements.push(`
                        <clipPath id="${clipId}">
                            <rect x="${x}" y="${currentY}" width="${colWidth}" height="${rowH}" rx="7" />
                        </clipPath>
                        <rect x="${x}" y="${currentY}" width="${colWidth}" height="${rowH}" rx="7" fill="${colors.busyBg}" />
                        <g clip-path="url(#${clipId})">
                            ${textElements.join('\n')}
                        </g>
                    `);
                } else {
                    // Свободное окно
                    tableElements.push(`
                        <rect x="${x}" y="${currentY}" width="${colWidth}" height="${rowH}" rx="7" fill="${colors.freeBg}" />
                        <text x="${x + colWidth / 2}" y="${currentY + rowH / 2 + 7}" fill="${colors.dashColor}" font-size="24" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">-</text>
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
    <text x="${width / 2}" y="${padY + 34}" fill="${colors.titleText}" font-size="22" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(title)}</text>
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
