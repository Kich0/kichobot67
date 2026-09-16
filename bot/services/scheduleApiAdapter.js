class ScheduleApiAdapter {
    constructor() {
        this.daysRu = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
        this.daysKz = ['Дүйсенбі', 'Сейсенбі', 'Сәрсенбі', 'Бейсенбі', 'Жұма', 'Сенбі'];
    }

    getDayIndex(record) {
        if (typeof record.dayIndex === 'number' && record.dayIndex >= 1 && record.dayIndex <= 7) {
            return record.dayIndex;
        }
        const name = (record.day || '').trim().toLowerCase();
        const map = {
            'понедельник': 1, 'дүйсенбі': 1, 'monday': 1,
            'вторник': 2, 'сейсенбі': 2, 'tuesday': 2,
            'среда': 3, 'сәрсенбі': 3, 'wednesday': 3,
            'четверг': 4, 'бейсенбі': 4, 'thursday': 4,
            'пятница': 5, 'жұма': 5, 'friday': 5,
            'суббота': 6, 'сенбі': 6, 'saturday': 6,
            'воскресенье': 7, 'жексенбі': 7, 'sunday': 7
        };
        return map[name] || 0;
    }

    normalizeTime(t) {
        if (!t) return '';
        return t
            .replace(/[:]/g, '.')
            .replace(/[–—]/g, '-')
            .replace(/(^|-)0(\d)/g, '$1$2')
            .trim();
    }

    formatLessonType(type) {
        if (!type) return '';
        const lower = type.toLowerCase().trim();
        if (lower.includes('лекц') || lower === 'лек') return '/Лекция/';
        if (lower.includes('прак') || lower.includes('семин') || lower === 'пр') return '/Прак.зан./';
        if (lower.includes('лаб')) return '/Лаб.раб./';
        if (lower.includes('сроп')) return '/СРОП/';
        return `/${type.trim()}/`;
    }

    formatLessonTypeBadge(type) {
        if (!type) return '';
        const lower = type.toLowerCase().trim();
        if (lower.includes('лекц') || lower === 'лек') return 'Лекция';
        if (lower.includes('прак') || lower.includes('семин') || lower === 'пр') return 'Прак.зан.';
        if (lower.includes('лаб')) return 'Лаб.раб.';
        if (lower.includes('сроп')) return 'СРОП';
        return type.trim();
    }

    formatRoom(room, building) {
        if (!room && !building) return '';
        if (room && building) {
            return `Ауд.${room}/${building}`;
        }
        return `Ауд.${room || building}`;
    }

    /**
     * Адаптация записей API в формат расписания студента для бота
     * @param {Array} records - Массив объектов records от API
     * @param {string} language - Язык расписания ('рус' или 'каз')
     * @returns {Array} - 6 дней недели со списком предметов
     */
    adaptGroupSchedule(records = [], language = 'рус') {
        const daysList = language === 'каз' ? this.daysKz : this.daysRu;
        const schedule = [];

        for (let dayNum = 1; dayNum <= 6; dayNum++) {
            const dayName = daysList[dayNum - 1];
            const dayRecords = records.filter(r => this.getDayIndex(r) === dayNum);

            // Сортируем по номеру слота или времени
            dayRecords.sort((a, b) => (a.slot || 0) - (b.slot || 0));

            // Группируем записи с одинаковым временем (например, подгруппы)
            const timeSlotMap = new Map();

            for (const r of dayRecords) {
                const time = this.normalizeTime(r.time || (r.slot ? `Пара ${r.slot}` : ''));
                if (!time) continue;

                const parts = [];
                if (r.subject) parts.push(r.subject.trim());
                if (r.type) parts.push(this.formatLessonType(r.type));
                if (r.teacher) parts.push(r.teacher.trim());
                const roomStr = this.formatRoom(r.room, r.building);
                if (roomStr) parts.push(roomStr);

                const subjectText = parts.join(' ');

                if (timeSlotMap.has(time)) {
                    // Если в это время уже есть пара (например, вторая подгруппа)
                    const existing = timeSlotMap.get(time);
                    timeSlotMap.set(time, `${existing}\n---\n${subjectText}`);
                } else {
                    timeSlotMap.set(time, subjectText);
                }
            }

            const subjects = [];
            for (const [time, subject] of timeSlotMap.entries()) {
                subjects.push({ time, subject });
            }

            schedule.push({
                day: dayName,
                subjects
            });
        }

        return schedule;
    }

    /**
     * Адаптация записей API в формат расписания преподавателя для бота
     * @param {Array} records - Массив объектов records от API
     * @returns {Array} - 6 дней недели со списком занятий преподавателя
     */
    adaptTeacherSchedule(records = []) {
        const schedule = [];

        for (let dayNum = 1; dayNum <= 6; dayNum++) {
            const dayName = this.daysRu[dayNum - 1];
            const dayRecords = records.filter(r => this.getDayIndex(r) === dayNum);

            dayRecords.sort((a, b) => (a.slot || 0) - (b.slot || 0));

            // Группируем по времени слота
            const timeSlotMap = new Map();

            for (const r of dayRecords) {
                const time = this.normalizeTime(r.time || (r.slot ? `Пара ${r.slot}` : ''));
                if (!time) continue;

                const roomStr = (r.room || r.building) ? ` (${r.room || ''}/${r.building || ''})` : '';
                const groupName = r.group || 'Занятие';

                if (timeSlotMap.has(time)) {
                    // Поточная лекция или несколько групп
                    const slotData = timeSlotMap.get(time);
                    if (!slotData.groupsList) {
                        slotData.groupsList = [slotData.group.replace(/\s*\([^)]*\)/g, '').trim()];
                    }
                    if (!slotData.groupsList.includes(groupName)) {
                        slotData.groupsList.push(groupName);
                        slotData.group = `${slotData.groupsList.join(', ')}${roomStr}`;
                    }
                    if (!slotData.subject && r.subject) {
                        slotData.subject = r.subject;
                    }
                    if (!slotData.lessonType && r.type) {
                        slotData.lessonType = this.formatLessonTypeBadge(r.type);
                    }
                } else {
                    timeSlotMap.set(time, {
                        time,
                        group: `${groupName}${roomStr}`,
                        groupsList: [groupName],
                        subject: r.subject || '',
                        lessonType: this.formatLessonTypeBadge(r.type) || '',
                        room: r.room || '',
                        building: r.building || ''
                    });
                }
            }

            const groups = Array.from(timeSlotMap.values());

            schedule.push({
                day: dayName,
                groups
            });
        }

        return schedule;
    }
}

export default new ScheduleApiAdapter();
