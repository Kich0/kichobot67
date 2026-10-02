import test from 'node:test';
import assert from 'node:assert/strict';
import ScheduleApiAdapter from '../bot/services/scheduleApiAdapter.js';

test('ScheduleApiAdapter normalizes times and formats rooms', () => {
    assert.equal(ScheduleApiAdapter.normalizeTime('08:30-09:20'), '8.30-9.20');
    assert.equal(ScheduleApiAdapter.normalizeTime('10:00 - 10:50'), '10.00-10.50');
    assert.equal(ScheduleApiAdapter.formatRoom('315', '3'), 'Ауд.315/3');
    assert.equal(ScheduleApiAdapter.formatRoom('204', ''), 'Ауд.204');
    assert.equal(ScheduleApiAdapter.formatLessonType('лекция'), '/Лекция/');
    assert.equal(ScheduleApiAdapter.formatLessonType('практика'), '/Прак.зан./');
});

test('ScheduleApiAdapter.adaptGroupSchedule produces 6 days with subjects', () => {
    const rawRecords = [
        {
            dayIndex: 1,
            time: '08:30-09:20',
            slot: 1,
            subject: 'Математика',
            type: 'лекция',
            teacher: 'Попова Н. В.',
            room: '315',
            building: '1'
        },
        {
            dayIndex: 1,
            time: '09:30-10:20',
            slot: 2,
            subject: 'Физика',
            type: 'практика',
            teacher: 'Нигай Е. В.',
            room: '102',
            building: '1'
        }
    ];

    const schedule = ScheduleApiAdapter.adaptGroupSchedule(rawRecords, 'рус');
    assert.equal(schedule.length, 6);
    assert.equal(schedule[0].day, 'Понедельник');
    assert.equal(schedule[0].subjects.length, 2);
    assert.ok(schedule[0].subjects[0].subject.includes('Математика'));
    assert.ok(schedule[0].subjects[1].subject.includes('Физика'));
});

test('ScheduleApiAdapter.adaptTeacherSchedule produces 6 days with time slots and groups', () => {
    const rawRecords = [
        {
            dayIndex: 2,
            time: '10:30-11:20',
            slot: 3,
            subject: 'Базы данных',
            type: 'лекция',
            group: 'ИТ-21-1',
            room: '210',
            building: '3'
        }
    ];

    const teacherSchedule = ScheduleApiAdapter.adaptTeacherSchedule(rawRecords);
    assert.equal(teacherSchedule.length, 6);
    assert.equal(teacherSchedule[1].day, 'Вторник');
    const matchedSlot = teacherSchedule[1].groups.find(g => g.time.includes('10.30') || g.time.includes('10:30'));
    assert.ok(matchedSlot);
    assert.equal(matchedSlot.subject, 'Базы данных');
    assert.ok(matchedSlot.group.includes('ИТ-21-1'));
});
