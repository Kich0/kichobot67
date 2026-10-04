import test from 'node:test';
import assert from 'node:assert/strict';
import ScheduleController from '../bot/controllers/ScheduleController.js';
import TeacherScheduleController from '../bot/controllers/TeacherScheduleController.js';

test('Schedule status indicator: 🟢 for API, 🔵 for MongoDB', async (t) => {
    const now = Date.now();

    await t.test('ScheduleController: fresh schedule from API shows 🟢', () => {
        const text = ScheduleController.formatElapsedTime(now, 'ru', false);
        assert.ok(text.startsWith('🟢'), `Expected 🟢, got: ${text}`);
    });

    await t.test('ScheduleController: schedule from MongoDB shows 🔵', () => {
        const text = ScheduleController.formatElapsedTime(now, 'ru', true);
        assert.ok(text.startsWith('🔵'), `Expected 🔵, got: ${text}`);
    });

    await t.test('ScheduleController: old schedule from MongoDB (3 days ago) still shows 🔵', () => {
        const oldTimestamp = now - 3 * 24 * 3600 * 1000;
        const text = ScheduleController.formatElapsedTime(oldTimestamp, 'ru', true);
        assert.ok(text.startsWith('🔵'), `Expected 🔵 for MongoDB even if old, got: ${text}`);
    });

    await t.test('ScheduleController: old schedule from API (6 hours) shows 🟡', () => {
        const sixHoursAgo = now - 6 * 3600 * 1000;
        const text = ScheduleController.formatElapsedTime(sixHoursAgo, 'ru', false);
        assert.ok(text.startsWith('🟡'), `Expected 🟡, got: ${text}`);
    });

    await t.test('ScheduleController: very old schedule from API (30 hours) shows 🔴', () => {
        const thirtyHoursAgo = now - 30 * 3600 * 1000;
        const text = ScheduleController.formatElapsedTime(thirtyHoursAgo, 'ru', false);
        assert.ok(text.startsWith('🔴'), `Expected 🔴, got: ${text}`);
    });

    await t.test('TeacherScheduleController: proxies formatElapsedTime with 🟢 for API and 🔵 for MongoDB', () => {
        const apiText = TeacherScheduleController.formatElapsedTime(now, 'ru', false);
        const dbText = TeacherScheduleController.formatElapsedTime(now, 'ru', true);
        assert.ok(apiText.startsWith('🟢'), `Teacher API expected 🟢, got: ${apiText}`);
        assert.ok(dbText.startsWith('🔵'), `Teacher DB expected 🔵, got: ${dbText}`);
    });
});
