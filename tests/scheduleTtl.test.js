import test from 'node:test';
import assert from 'node:assert/strict';
import { Schedule } from '../bot/models/schedule.js';
import { TeacherSchedule } from '../bot/models/teacherSchedule.js';
import { UserAction } from '../bot/models/userAction.js';

test('Schedule and TeacherSchedule models have NO TTL index (permanent storage)', () => {
    const scheduleIndexes = Schedule.schema.indexes();
    const hasScheduleTTL = scheduleIndexes.some(([, opts]) => opts?.expireAfterSeconds !== undefined);
    assert.equal(hasScheduleTTL, false, 'Schedule must NOT have any TTL index');

    const teacherIndexes = TeacherSchedule.schema.indexes();
    const hasTeacherTTL = teacherIndexes.some(([, opts]) => opts?.expireAfterSeconds !== undefined);
    assert.equal(hasTeacherTTL, false, 'TeacherSchedule must NOT have any TTL index');
});

test('UserAction model has 30-day TTL index on createdAt', () => {
    const actionIndexes = UserAction.schema.indexes();
    const ttlIndex = actionIndexes.find(([spec, opts]) => spec.createdAt === 1 && opts?.expireAfterSeconds === 30 * 24 * 60 * 60);
    assert.ok(ttlIndex, 'UserAction must have 30-day TTL index on createdAt (2592000s)');
    assert.equal(ttlIndex[1].expireAfterSeconds, 2592000);
});
