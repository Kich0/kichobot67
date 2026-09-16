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

function splitSubjectText(text, maxLine = 16) {
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
    for (const w of words) {
        if (!curLine) {
            curLine = w;
        } else if ((curLine + ' ' + w).length <= maxLine) {
            curLine += ' ' + w;
        } else {
            lines.push(curLine);
            curLine = w;
            if (lines.length >= 2) break;
        }
    }
    if (curLine && lines.length < 2) lines.push(curLine);
    return lines;
}

class TeacherTableImageService {
    constructor() {
        // LRU Cache: Map preserves insertion order
        // Key: teacherId (String/Number)
        // Value: { buffer: Buffer, expiresAt: number }
        this.cache = new Map();
        this.MAX_ENTRIES = 200; // ~28-30 MB максимум в RAM
        this.TTL = 30 * 60 * 1000; // 30 минут
    }

    /**
     * Построение чистого минималистичного SVG в стилистике сайта КарУ
     */
    buildSvg(teacher, scheduleData, lang = 'ru') {
        const colors = {
            canvasBg: '#ffffff',
            border: '#cbd5e1',
            titleText: '#0f172a',
            headerBg: '#64748b',
            headerText: '#ffffff',
            headerSub: '#f8fafc',
            dayBg: '#e2e8f0',
            dayText: '#1e293b',
            busyBg: '#16a34a',
            busyBorder: '#15803d',
            groupText: '#ffffff',
            subjectText: '#dcfce7',
            freeBg: '#dc2626',
            freeBorder: '#b91c1c',
            dashColor: '#ffffff'
        };

        const standardTimes = [
            "8.30-9.20", "9.30-10.20", "10.40-11.30", "11.40-12.30",
            "12.40-13.30", "13.40-14.30", "14.40-15.30", "15.50-16.40",
            "16.50-17.40", "17.50-18.40"
        ];

        const activeTimes = standardTimes.filter(t => {
            return scheduleData.some(d => d.groups?.some(g => g.time === t && g.group && g.group.trim()));
        });
        const times = activeTimes.length > 0 ? activeTimes : standardTimes.slice(0, 8);

        const padX = 16;
        const padY = 16;
        const titleHeight = 50;
        const colHeaderHeight = 44;
        const dayColWidth = 135;
        const colWidth = 126;
        const rowHeight = 74;
        const cellGap = 4;

        const numCols = times.length;
        const numRows = scheduleData.length;

        const width = padX * 2 + dayColWidth + numCols * (colWidth + cellGap) - cellGap;
        const height = padY * 2 + titleHeight + colHeaderHeight + numRows * (rowHeight + cellGap);

        const startTableY = padY + titleHeight;
        let tableElements = [];

        // Шапка "День \ Время"
        const dayTimeLabel = lang === 'kz' ? 'Күн \\ Уақыт' : 'День \\ Время';
        tableElements.push(`
            <rect x="${padX}" y="${startTableY}" width="${dayColWidth}" height="${colHeaderHeight}" rx="6" fill="${colors.headerBg}" />
            <text x="${padX + dayColWidth / 2}" y="${startTableY + 27}" fill="${colors.headerText}" font-size="12" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${dayTimeLabel}</text>
        `);

        // Колонки времени пар
        const pairWord = lang === 'kz' ? 'сабақ' : 'пара';
        times.forEach((t, i) => {
            const x = padX + dayColWidth + cellGap + i * (colWidth + cellGap);
            tableElements.push(`
                <rect x="${x}" y="${startTableY}" width="${colWidth}" height="${colHeaderHeight}" rx="6" fill="${colors.headerBg}" />
                <text x="${x + colWidth / 2}" y="${startTableY + 18}" fill="${colors.headerSub}" font-size="11.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${i + 1} ${pairWord}</text>
                <text x="${x + colWidth / 2}" y="${startTableY + 33}" fill="${colors.headerText}" font-size="10.5" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${t}</text>
            `);
        });

        // Строки дней недели
        scheduleData.forEach((day, rIdx) => {
            const y = startTableY + colHeaderHeight + cellGap + rIdx * (rowHeight + cellGap);

            tableElements.push(`
                <rect x="${padX}" y="${y}" width="${dayColWidth}" height="${rowHeight}" rx="6" fill="${colors.dayBg}" />
                <text x="${padX + dayColWidth / 2}" y="${y + rowHeight / 2 + 5}" fill="${colors.dayText}" font-size="13.5" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(day.day)}</text>
            `);

            times.forEach((t, cIdx) => {
                const x = padX + dayColWidth + cellGap + cIdx * (colWidth + cellGap);
                const slot = day.groups?.find(g => g.time === t);
                const isBusy = slot && slot.group && slot.group.trim();

                if (isBusy) {
                    const groupRaw = slot.group.trim();
                    const subjectLines = slot.subject ? splitSubjectText(slot.subject, 15) : [];

                    let linesHtml = '';
                    if (subjectLines.length > 0) {
                        linesHtml += `<text x="${x + colWidth / 2}" y="${y + 22}" fill="${colors.groupText}" font-size="12" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(groupRaw)}</text>`;
                        linesHtml += `<text x="${x + colWidth / 2}" y="${y + 39}" fill="${colors.subjectText}" font-size="10.5" font-weight="600" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(subjectLines[0])}</text>`;
                        if (subjectLines[1]) {
                            linesHtml += `<text x="${x + colWidth / 2}" y="${y + 54}" fill="${colors.subjectText}" font-size="10.5" font-weight="600" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(subjectLines[1])}</text>`;
                        }
                    } else {
                        linesHtml += `<text x="${x + colWidth / 2}" y="${y + rowHeight / 2 + 5}" fill="${colors.groupText}" font-size="13" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(groupRaw)}</text>`;
                    }

                    tableElements.push(`
                        <rect x="${x}" y="${y}" width="${colWidth}" height="${rowHeight}" rx="6" fill="${colors.busyBg}" />
                        ${linesHtml}
                    `);
                } else {
                    tableElements.push(`
                        <rect x="${x}" y="${y}" width="${colWidth}" height="${rowHeight}" rx="6" fill="${colors.freeBg}" />
                        <text x="${x + colWidth / 2}" y="${y + rowHeight / 2 + 6}" fill="${colors.dashColor}" font-size="22" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">-</text>
                    `);
                }
            });
        });

        const cleanName = (teacher?.name || 'Преподаватель').replace(/^преп\.\s*/i, '');
        const titlePrefix = lang === 'kz' ? 'Оқытушының жүктемесі' : 'Загруженность преп.';
        const title = `${titlePrefix} ${cleanName}`;

        return `
<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
    <rect width="${width}" height="${height}" rx="10" fill="${colors.canvasBg}" stroke="${colors.border}" stroke-width="1" />
    <text x="${width / 2}" y="${padY + 28}" fill="${colors.titleText}" font-size="20" font-weight="bold" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">${escapeXml(title)}</text>
    ${tableElements.join('\n')}
</svg>
        `;
    }

    /**
     * Получить PNG-буфер таблицы расписания (с кэшированием в памяти до 30 МБ)
     * @param {Object} teacher
     * @param {Array} scheduleData
     * @param {String} lang
     * @returns {Promise<Buffer>}
     */
    async getTeacherTableImage(teacher, scheduleData, lang = 'ru') {
        const teacherId = teacher?.id || teacher?._id || 'unknown';
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

        // 2. Генерируем SVG
        const svg = this.buildSvg(teacher, scheduleData, lang);

        // 3. Рендерим PNG через @resvg/resvg-js
        const resvg = new Resvg(svg, {
            fitTo: { mode: 'zoom', value: 1.2 }
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
