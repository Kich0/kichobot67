import test from 'node:test';
import assert from 'node:assert/strict';
import { ScheduleSnapshot } from '../bot/models/scheduleSnapshot.js';
import { Schedule } from '../bot/models/schedule.js';
import { TeacherSchedule } from '../bot/models/teacherSchedule.js';

test('ScheduleSnapshot model has 5-day TTL index and no duplicate indexes', () => {
    const indexes = ScheduleSnapshot.schema.indexes();
    // Check groupId index
    const groupIdIndex = indexes.find(([spec]) => spec.groupId === 1);
    assert.ok(groupIdIndex, 'groupId index must exist');

    // Check createdAt TTL index
    const ttlIndexes = indexes.filter(([spec, opts]) => spec.createdAt === 1 && opts.expireAfterSeconds === 5 * 24 * 60 * 60);
    assert.equal(ttlIndexes.length, 1, 'Exactly one 5-day TTL index on createdAt must exist without duplicates');
    assert.equal(ttlIndexes[0][1].expireAfterSeconds, 432000);
});

test('Schedule and TeacherSchedule models have NO TTL index (indestructible fallback)', () => {
    const scheduleIndexes = Schedule.schema.indexes();
    const hasScheduleTTL = scheduleIndexes.some(([, opts]) => opts?.expireAfterSeconds !== undefined);
    assert.equal(hasScheduleTTL, false, 'Schedule must NOT have any TTL index');

    const teacherIndexes = TeacherSchedule.schema.indexes();
    const hasTeacherTTL = teacherIndexes.some(([, opts]) => opts?.expireAfterSeconds !== undefined);
    assert.equal(hasTeacherTTL, false, 'TeacherSchedule must NOT have any TTL index');
});
