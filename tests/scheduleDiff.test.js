import test from 'node:test';
import assert from 'node:assert/strict';
import {
    findInlineDiff,
    compareStudentSchedules,
    compareTeacherSchedules,
    formatDetailedDiffText,
    generateGroupDiffDossier,
    generateTeacherDiffDossier
} from '../bot/services/scheduleDiffService.js';
import { ScheduleVersion } from '../bot/models/schedule.js';
import { TeacherScheduleVersion } from '../bot/models/teacherSchedule.js';

test('findInlineDiff detects classroom and character changes', () => {
    // 1. Изменение одной цифры в аудитории
    const diff1 = findInlineDiff(
        "Высшая математика /Лекция/\nИванов И.И. Ауд.305/1",
        "Высшая математика /Лекция/\nИванов И.И. Ауд.307/1"
    );
    assert.ok(diff1, "Diff must be detected");
    assert.ok(diff1.summary.includes("305") && diff1.summary.includes("307"), "Diff summary must highlight 305 -> 307");

    // 2. Идентичные строки
    const diffIdentical = findInlineDiff("Физика", "Физика");
    assert.equal(diffIdentical, null, "Identical strings must return null");

    // 3. Замена типа занятия
    const diffType = findInlineDiff(
        "Базы данных /Лекция/ Ауд.210",
        "Базы данных /Прак.зан./ Ауд.210"
    );
    assert.ok(diffType, "Type change must be detected");
    assert.ok(diffType.summary.includes("Лекция") && diffType.summary.includes("Прак.зан."));
});

test('compareStudentSchedules detects modifications, additions, deletions, and moves', () => {
    const oldSchedule = [
        {
            day: "Понедельник",
            subjects: [
                { time: "8.30-9.20", subject: "Физика /Лекция/ Ауд.305/1" },
                { time: "10.00-10.50", subject: "Химия /Лаб.раб./ Ауд.101" }
            ]
        },
        {
            day: "Вторник",
            subjects: [
                { time: "8.30-9.20", subject: "История Казахстана /Лекция/ Ауд.402" }
            ]
        }
    ];

    const newSchedule = [
        {
            day: "Понедельник",
            subjects: [
                // Изменена аудитория: 305/1 -> 307/1
                { time: "8.30-9.20", subject: "Физика /Лекция/ Ауд.307/1" },
                // Новая добавленная пара в 11.30
                { time: "11.30-12.20", subject: "Английский язык /Прак.зан./ Ауд.205" }
                // Химия снята
            ]
        },
        {
            day: "Вторник",
            subjects: [
                { time: "8.30-9.20", subject: "История Казахстана /Лекция/ Ауд.402" }
            ]
        }
    ];

    const res = compareStudentSchedules(oldSchedule, newSchedule);
    assert.equal(res.hasChanges, true);
    assert.equal(res.changesCount, 3); // 1 mod, 1 add, 1 remove

    const mod = res.changes.find(c => c.type === "MODIFIED");
    assert.ok(mod, "Must have MODIFIED item");
    assert.equal(mod.day, "Понедельник");
    assert.equal(mod.time, "8.30-9.20");
    assert.ok(mod.diffText.includes("305") && mod.diffText.includes("307"));

    const add = res.changes.find(c => c.type === "ADDED");
    assert.ok(add, "Must have ADDED item");
    assert.equal(add.time, "11.30-12.20");

    const rem = res.changes.find(c => c.type === "REMOVED");
    assert.ok(rem, "Must have REMOVED item");
    assert.equal(rem.time, "10.00-10.50");
});

test('compareStudentSchedules detects identical schedules without false positives', () => {
    const sched = [
        {
            day: "Понедельник",
            subjects: [{ time: "8.30-9.20", subject: "Математика Ауд.301" }]
        }
    ];
    const res = compareStudentSchedules(sched, sched);
    assert.equal(res.hasChanges, false);
    assert.equal(res.changesCount, 0);
    assert.equal(res.summary, "Без изменений");
});

test('compareTeacherSchedules detects subject, group, room, and lessonType differences', () => {
    const oldTeacher = [
        {
            day: "Среда",
            groups: [
                { time: "8.30-9.20", group: "ИС-21-1 (305/1)", subject: "Информатика", lessonType: "Лекция" },
                { time: "10.00-10.50", group: "ВТ-22-2 (210)", subject: "Программирование", lessonType: "Лаб.раб." }
            ]
        }
    ];

    const newTeacher = [
        {
            day: "Среда",
            groups: [
                // Изменилась аудитория группы: 305/1 -> 307/1
                { time: "8.30-9.20", group: "ИС-21-1 (307/1)", subject: "Информатика", lessonType: "Лекция" },
                // Изменился тип занятия
                { time: "10.00-10.50", group: "ВТ-22-2 (210)", subject: "Программирование", lessonType: "Прак.зан." }
            ]
        }
    ];

    const res = compareTeacherSchedules(oldTeacher, newTeacher);
    assert.equal(res.hasChanges, true);
    assert.equal(res.changesCount, 2);

    const slot1 = res.changes.find(c => c.time === "8.30-9.20");
    assert.ok(slot1.diffText.includes("305") && slot1.diffText.includes("307"));

    const slot2 = res.changes.find(c => c.time === "10.00-10.50");
    assert.ok(slot2.diffText.includes("Лаб.раб.") && slot2.diffText.includes("Прак.зан."));
});

test('formatDetailedDiffText formats changes with visual indicators', () => {
    const changes = [
        {
            day: "Понедельник",
            time: "8.30-9.20",
            type: "MODIFIED",
            typeLabel: "✏️ Изменение",
            oldValue: "Высшая математика Ауд.305/1",
            newValue: "Высшая математика Ауд.307/1",
            diffText: "[305/1 ➔ 307/1]"
        }
    ];
    const text = formatDetailedDiffText(changes);
    assert.ok(text.includes("Понедельник"));
    assert.ok(text.includes("8.30-9.20"));
    assert.ok(text.includes("❌ Было"));
    assert.ok(text.includes("✅ Стало"));
    assert.ok(text.includes("[305/1 ➔ 307/1]"));
});

test('generateGroupDiffDossier and generateTeacherDiffDossier create complete reports', () => {
    const groupDossier = generateGroupDiffDossier({
        groupName: "ИС-21-1",
        groupId: 105,
        faculty: "ФМИТ",
        activeSchedule: {
            data: [
                { day: "Понедельник", subjects: [{ time: "8.30-9.20", subject: "Математика" }] }
            ]
        },
        versions: [
            {
                archivedAtFormatted: "14.10.2026 12:00:00",
                validFromFormatted: "01.09.2026",
                validToFormatted: "14.10.2026",
                hash: "abc123hash",
                diffSummary: "Пн 8.30: замена аудитории [305 ➔ 307]",
                diffDetails: [
                    {
                        day: "Понедельник",
                        time: "8.30-9.20",
                        type: "MODIFIED",
                        typeLabel: "✏️ Изменение",
                        oldValue: "Математика Ауд.305",
                        newValue: "Математика Ауд.307",
                        diffText: "[305 ➔ 307]"
                    }
                ]
            }
        ]
    });
    assert.ok(groupDossier.includes("ИС-21-1 (ID: 105)"));
    assert.ok(groupDossier.includes("ТЕКУЩЕЕ АКТУАЛЬНОЕ РАСПИСАНИЕ"));
    assert.ok(groupDossier.includes("ХРОНОЛОГИЯ И ДЕТАЛЬНЫЕ ИЗМЕНЕНИЯ"));
    assert.ok(groupDossier.includes("[305 ➔ 307]"));

    const teacherDossier = generateTeacherDiffDossier({
        teacherName: "Иванов И.И.",
        teacherId: 204,
        department: "Кафедра физики",
        activeSchedule: { data: [] },
        versions: []
    });
    assert.ok(teacherDossier.includes("Иванов И.И. (ID: 204)"));
    assert.ok(teacherDossier.includes("Для этого преподавателя изменений расписания ещё не происходило"));
});

test('ScheduleVersion and TeacherScheduleVersion models have diffSummary and diffDetails fields', () => {
    const sSchema = ScheduleVersion.schema.paths;
    assert.ok(sSchema.diffSummary, "ScheduleVersion must have diffSummary path");
    assert.ok(sSchema.diffDetails, "ScheduleVersion must have diffDetails path");

    const tSchema = TeacherScheduleVersion.schema.paths;
    assert.ok(tSchema.diffSummary, "TeacherScheduleVersion must have diffSummary path");
    assert.ok(tSchema.diffDetails, "TeacherScheduleVersion must have diffDetails path");
});
