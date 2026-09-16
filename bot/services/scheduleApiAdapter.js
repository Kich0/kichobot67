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

                let lines = [];
                if (r.subject) lines.push(r.subject);
                
                let details = [];
                if (r.teacher) details.push(r.teacher);
                if (r.room || r.building) {
                    details.push(`(${r.room || ''}/${r.building || ''})`);
                }
                if (details.length) lines.push(details.join(' '));

                if (r.type) lines.push(`(${r.type})`);

                const subjectText = lines.join('\n');

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
                    if (!slotData.group.includes(groupName)) {
                        slotData.group = `${slotData.group.replace(/\s*\([^)]*\)/, '')}, ${groupName}${roomStr}`;
                    }
                    if (!slotData.subject && r.subject) {
                        slotData.subject = r.subject;
                    }
                } else {
                    timeSlotMap.set(time, {
                        time,
                        group: `${groupName}${roomStr}`,
                        subject: r.subject || '',
                        lessonType: r.type || '',
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
