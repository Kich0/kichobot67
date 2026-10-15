import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');

import ScheduleService, { keepAliveHttpsAgent, keepAliveHttpAgent, sleep } from '../backend/services/ScheduleService.js';
import TeacherScheduleService from '../backend/services/TeacherScheduleService.js';
import BuketovApiService from '../bot/services/buketovApiService.js';
import groupService from '../bot/services/groupService.js';
import teacherService from '../bot/services/teacherService.js';
import { Group } from '../bot/models/group.js';
import { Teacher } from '../bot/models/teacher.js';
import { Schedule } from '../bot/models/schedule.js';
import { TeacherSchedule } from '../bot/models/teacherSchedule.js';

test('ScheduleService: keep-alive agents and sleep helper exist', () => {
    assert.ok(keepAliveHttpsAgent, 'keepAliveHttpsAgent must exist');
    assert.equal(keepAliveHttpsAgent.keepAlive, true, 'https agent keepAlive must be true');
    assert.ok(keepAliveHttpAgent, 'keepAliveHttpAgent must exist');
    assert.equal(keepAliveHttpAgent.keepAlive, true, 'http agent keepAlive must be true');
    assert.equal(typeof sleep, 'function', 'sleep helper must be exported');
});

test('ScheduleService: legacy scraper stubs return empty arrays gracefully', async () => {
    const faculties = await ScheduleService.get_faculty_list();
    assert.deepEqual(faculties, []);

    const progs = await ScheduleService.get_programs_by_faculty_name();
    assert.deepEqual(progs, []);

    const progsByFacId = await ScheduleService.get_program_list_by_facultyId();
    assert.deepEqual(progsByFacId, []);

    const groupsByProgId = await ScheduleService.get_group_list_by_programId();
    assert.deepEqual(groupsByProgId, []);

    const allGroups = await ScheduleService.get_all_groups_fast();
    assert.deepEqual(allGroups, []);
});

test('TeacherScheduleService: legacy scraper stubs return empty arrays gracefully', async () => {
    const depts = await TeacherScheduleService.get_departments_list();
    assert.deepEqual(depts, []);

    const teachers = await TeacherScheduleService.get_teachers_list(99);
    assert.deepEqual(teachers, []);
});

test('ScheduleService.get_schedule_by_groupId: returns adapted schedule when BuketovApiService succeeds', async () => {
    const origGetById = groupService.getById;
    const origGetGroupSchedule = BuketovApiService.getGroupSchedule;

    try {
        groupService.getById = async (id) => ({ id: Number(id), name: 'ФИЗ-21-1' });
        BuketovApiService.getGroupSchedule = async (groupName) => ({
            records: [
                { day: 'понедельник', slot: 1, time: '08:30-09:50', discipline: 'Физика', teacher: 'Иванов И. И.', room: '301', building: '1' }
            ],
            total: 1,
            _fromDb: false
        });

        // Test with string ID as well as number ID
        const result = await ScheduleService.get_schedule_by_groupId('123', 'рус');
        assert.ok(Array.isArray(result), 'Result should be an array of days');
        assert.equal(result.length, 6, 'Must contain 6 days');
        assert.equal(result._fromDb, false);
        const monday = result.find(d => d.day && d.day.toLowerCase() === 'понедельник');
        assert.ok(monday, 'Monday should exist');
        assert.ok(monday.subjects.length > 0, 'Monday should have subjects');
    } finally {
        groupService.getById = origGetById;
        BuketovApiService.getGroupSchedule = origGetGroupSchedule;
    }
});

test('ScheduleService.get_schedule_by_groupId: throws NO_SCHEDULE when API returns empty records and not from DB', async () => {
    const origGetById = groupService.getById;
    const origGetGroupSchedule = BuketovApiService.getGroupSchedule;

    try {
        groupService.getById = async (id) => ({ id: Number(id), name: 'ПУСТ-21-1' });
        BuketovApiService.getGroupSchedule = async () => ({
            records: [],
            total: 0,
            _fromDb: false
        });

        await assert.rejects(
            async () => await ScheduleService.get_schedule_by_groupId(456, 'рус'),
            (err) => {
                assert.equal(err.code, 'NO_SCHEDULE');
                assert.equal(err.groupName, 'ПУСТ-21-1');
                return true;
            }
        );
    } finally {
        groupService.getById = origGetById;
        BuketovApiService.getGroupSchedule = origGetGroupSchedule;
    }
});

test('ScheduleService.get_schedule_by_groupId: falls back to MongoDB when API throws', async () => {
    const origGetById = groupService.getById;
    const origGetGroupSchedule = BuketovApiService.getGroupSchedule;
    const origFindOne = Schedule.findOne;

    try {
        groupService.getById = async (id) => ({ id: Number(id), name: 'ТЕСТ-21-1' });
        BuketovApiService.getGroupSchedule = async () => {
            throw new Error('Network timeout connecting to Buketov API');
        };

        const mockScheduleData = [
            { day: 'понедельник', subjects: [{ time: '08:30-09:50', subject: 'Математика' }] }
        ];

        Schedule.findOne = (query) => ({
            lean: () => Promise.resolve({
                groupId: query.groupId,
                data: mockScheduleData,
                updatedAt: new Date('2026-10-15T10:00:00Z')
            })
        });

        const result = await ScheduleService.get_schedule_by_groupId('789', 'рус');
        assert.ok(Array.isArray(result));
        assert.equal(result._fromDb, true);
        assert.ok(result._updatedAt);
    } finally {
        groupService.getById = origGetById;
        BuketovApiService.getGroupSchedule = origGetGroupSchedule;
        Schedule.findOne = origFindOne;
    }
});

test('TeacherScheduleService.get_teacher_schedule: returns adapted schedule when BuketovApiService succeeds', async () => {
    const origGetById = teacherService.getById;
    const origGetTeacherSchedule = BuketovApiService.getTeacherSchedule;

    try {
        teacherService.getById = async (id) => ({ id: Number(id), name: 'Ахметов А. А.' });
        BuketovApiService.getTeacherSchedule = async () => ({
            records: [
                { day: 'вторник', slot: 2, time: '10:00-11:20', discipline: 'Информатика', group: 'ИС-21-1', room: '205', building: '3' }
            ],
            total: 1,
            _fromDb: false
        });

        const result = await TeacherScheduleService.get_teacher_schedule('55', 'рус');
        assert.ok(Array.isArray(result), 'Result must be an array of days');
        assert.equal(result.length, 6, 'Must contain 6 days');
        assert.equal(result._fromDb, false);
        const tuesday = result.find(d => d.day && d.day.toLowerCase() === 'вторник');
        assert.ok(tuesday);
        assert.ok(tuesday.groups.length > 0);
    } finally {
        teacherService.getById = origGetById;
        BuketovApiService.getTeacherSchedule = origGetTeacherSchedule;
    }
});

test('TeacherScheduleService.get_teacher_schedule: returns empty 6-day grid when API has 0 records', async () => {
    const origGetById = teacherService.getById;
    const origGetTeacherSchedule = BuketovApiService.getTeacherSchedule;

    try {
        teacherService.getById = async (id) => ({ id: Number(id), name: 'Свободный С. С.' });
        BuketovApiService.getTeacherSchedule = async () => ({
            records: [],
            total: 0,
            _fromDb: false
        });

        const result = await TeacherScheduleService.get_teacher_schedule(77, 'рус');
        assert.ok(Array.isArray(result));
        assert.equal(result.length, 6);
        assert.equal(result._fromDb, false);
        // All days should have empty groups
        assert.ok(result.every(d => Array.isArray(d.groups) && d.groups.length === 0));
    } finally {
        teacherService.getById = origGetById;
        BuketovApiService.getTeacherSchedule = origGetTeacherSchedule;
    }
});

test('TeacherScheduleService.get_teacher_schedule: falls back to MongoDB when API throws', async () => {
    const origGetById = teacherService.getById;
    const origGetTeacherSchedule = BuketovApiService.getTeacherSchedule;
    const origFindOne = TeacherSchedule.findOne;

    try {
        teacherService.getById = async (id) => ({ id: Number(id), name: 'Резерв Р. Р.' });
        BuketovApiService.getTeacherSchedule = async () => {
            throw new Error('API down');
        };

        const mockTeacherData = [
            { day: 'среда', groups: [{ time: '08:30-09:50', group: 'ФИЗ-21-1 (301/1)', subject: 'Оптика' }] }
        ];

        TeacherSchedule.findOne = (query) => ({
            lean: () => Promise.resolve({
                teacherId: query.teacherId,
                schedule: mockTeacherData,
                updatedAt: new Date('2026-10-14T08:00:00Z')
            })
        });

        const result = await TeacherScheduleService.get_teacher_schedule('88', 'рус');
        assert.ok(Array.isArray(result));
        assert.equal(result._fromDb, true);
        assert.ok(result._updatedAt);
    } finally {
        teacherService.getById = origGetById;
        BuketovApiService.getTeacherSchedule = origGetTeacherSchedule;
        TeacherSchedule.findOne = origFindOne;
    }
});

test('TeacherScheduleService.get_teacher_schedule: returns empty 6-day grid when API throws and MongoDB is empty', async () => {
    const origGetById = teacherService.getById;
    const origGetTeacherSchedule = BuketovApiService.getTeacherSchedule;
    const origFindOne = TeacherSchedule.findOne;

    try {
        teacherService.getById = async (id) => ({ id: Number(id), name: 'Неизвестный Н. Н.' });
        BuketovApiService.getTeacherSchedule = async () => {
            throw new Error('API down');
        };

        TeacherSchedule.findOne = () => ({
            lean: () => Promise.resolve(null)
        });

        const result = await TeacherScheduleService.get_teacher_schedule(999, 'рус');
        assert.ok(Array.isArray(result));
        assert.equal(result.length, 6);
        assert.ok(result.every(d => Array.isArray(d.groups) && d.groups.length === 0));
    } finally {
        teacherService.getById = origGetById;
        BuketovApiService.getTeacherSchedule = origGetTeacherSchedule;
        TeacherSchedule.findOne = origFindOne;
    }
});

test('Dead legacy files were completely removed from backend/', () => {
    const deadFiles = [
        'backend/services/FreeProxyService.js',
        'backend/services/KsuAuthService.js',
        'backend/services/BrowserService.js',
        'backend/services/HtmlService.js',
        'backend/cron/ksuReAuth.js',
        'backend/controllers/BrowserController.js',
        'backend/controllers/TeacherController.js',
        'backend/controllers/ScheduleController.js',
        'backend/controllers/TeacherScheduleController.js',
        'backend/routers/browserRouter.js',
        'backend/routers/scheduleRouter.js',
        'backend/routers/teacherRouter.js',
        'backend/routers/teacherScheduleRouter.js',
        'backend/router.js'
    ];

    for (const relPath of deadFiles) {
        const fullPath = path.resolve(rootDir, relPath);
        assert.equal(fs.existsSync(fullPath), false, `File ${relPath} must not exist`);
    }
});

test('Backup folder _legacy_code_backup exists and contains all backed up files', () => {
    const backupDir = path.resolve(rootDir, '_legacy_code_backup');
    assert.ok(fs.existsSync(backupDir), '_legacy_code_backup must exist');

    const expectedInBackup = [
        '_legacy_code_backup/backend/services/FreeProxyService.js',
        '_legacy_code_backup/backend/services/KsuAuthService.js',
        '_legacy_code_backup/backend/services/BrowserService.js',
        '_legacy_code_backup/backend/services/HtmlService.js',
        '_legacy_code_backup/backend/cron/ksuReAuth.js',
        '_legacy_code_backup/backend/controllers/BrowserController.js',
        '_legacy_code_backup/backend/controllers/TeacherController.js',
        '_legacy_code_backup/backend/controllers/ScheduleController.js',
        '_legacy_code_backup/backend/controllers/TeacherScheduleController.js',
        '_legacy_code_backup/backend/router.js',
        '_legacy_code_backup/backend/services/ScheduleService.js',
        '_legacy_code_backup/backend/services/TeacherScheduleService.js',
        '_legacy_code_backup/package.json',
        '_legacy_code_backup/server.js'
    ];

    for (const relPath of expectedInBackup) {
        const fullPath = path.resolve(rootDir, relPath);
        assert.ok(fs.existsSync(fullPath), `Backup file ${relPath} must exist`);
    }
});

test('package.json does not contain removed dependencies', () => {
    const pkgPath = path.resolve(rootDir, 'package.json');
    const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
    const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };

    assert.equal(allDeps['cheerio'], undefined, 'cheerio must be removed');
    assert.equal(allDeps['body-parser'], undefined, 'body-parser must be removed');
    assert.equal(allDeps['http-proxy-agent'], undefined, 'http-proxy-agent must be removed');
    assert.equal(allDeps['https-proxy-agent'], undefined, 'https-proxy-agent must be removed');
});

test('git exclusions include _legacy_code_backup', () => {
    const gitignore = fs.readFileSync(path.resolve(rootDir, '.gitignore'), 'utf8');
    assert.ok(gitignore.includes('_legacy_code_backup/'), '.gitignore must contain _legacy_code_backup/');

    const exclude = fs.readFileSync(path.resolve(rootDir, '.git/info/exclude'), 'utf8');
    assert.ok(exclude.includes('_legacy_code_backup/'), '.git/info/exclude must contain _legacy_code_backup/');
});
