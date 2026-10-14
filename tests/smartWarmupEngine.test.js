import test from 'node:test';
import assert from 'node:assert/strict';
import { isWarmupActiveKZ, SmartWarmupEngine, KZ_OFFSET_MS } from '../bot/cron/scheduleCacheWarmup.js';
import buketovApiService from '../bot/services/buketovApiService.js';

test('isWarmupActiveKZ: correctly classifies all 24 hours in Asia/Almaty (UTC+5)', () => {
    // Helper to create a Date corresponding to a specific KZ hour
    // Since KZ = UTC + 5, UTC hour = (kzHour - 5 + 24) % 24
    function makeKzDate(kzHour, minute = 0, second = 0) {
        const utcHour = (kzHour - 5 + 24) % 24;
        const d = new Date(Date.UTC(2026, 9, 15, utcHour, minute, second));
        return d;
    }

    // Active hours: 06:00 to 01:59 KZ
    // Sleep hours: 02:00 to 05:59 KZ
    for (let h = 0; h < 24; h++) {
        const d = makeKzDate(h, 30);
        const active = isWarmupActiveKZ(d);
        if (h >= 2 && h < 6) {
            assert.equal(active, false, `Hour ${h}:30 KZ must be NIGHT SLEEP (false)`);
        } else {
            assert.equal(active, true, `Hour ${h}:30 KZ must be ACTIVE (true)`);
        }
    }

    // Test exact boundary moments
    // 01:59:59 KZ -> active
    assert.equal(isWarmupActiveKZ(makeKzDate(1, 59, 59)), true);
    // 02:00:00 KZ -> night sleep starts
    assert.equal(isWarmupActiveKZ(makeKzDate(2, 0, 0)), false);
    // 05:59:59 KZ -> still night sleep
    assert.equal(isWarmupActiveKZ(makeKzDate(5, 59, 59)), false);
    // 06:00:00 KZ -> morning wake up
    assert.equal(isWarmupActiveKZ(makeKzDate(6, 0, 0)), true);
});

test('SmartWarmupEngine: initialized with 4s group interval, 10s teacher interval, and 2h top cache TTL', () => {
    const engine = new SmartWarmupEngine();
    assert.equal(engine.GROUP_INTERVAL_MS, 4000);
    assert.equal(engine.TEACHER_INTERVAL_MS, 10000);
    assert.equal(engine.TOP_GROUPS_LIMIT, 150);
    assert.equal(engine.TOP_TEACHERS_LIMIT, 30);
    assert.equal(engine.TOP_LISTS_CACHE_TTL, 2 * 60 * 60 * 1000);
    assert.equal(engine.groupCursor, 0);
    assert.equal(engine.teacherCursor, 0);
    assert.equal(engine.isRunning, false);
});

test('SmartWarmupEngine: skips tick if buketovApiService.canMakeApiCall() is false', async () => {
    const engine = new SmartWarmupEngine();
    let warmupCalled = false;

    engine.warmupSingleGroup = async () => {
        warmupCalled = true;
    };
    engine.topGroups = [{ id: 101, name: 'IS-21' }];
    engine.lastTopGroupsFetchAt = Date.now();

    // Mock canMakeApiCall to return false (e.g. rate limit full or API_DOWN)
    const originalCanMakeApiCall = buketovApiService.canMakeApiCall;
    buketovApiService.canMakeApiCall = () => false;

    try {
        await engine.tickGroup();
        assert.equal(warmupCalled, false, 'tickGroup must be skipped when canMakeApiCall returns false');
    } finally {
        buketovApiService.canMakeApiCall = originalCanMakeApiCall;
    }
});

test('SmartWarmupEngine: cycles sequentially through groups and teachers', async () => {
    const engine = new SmartWarmupEngine();
    const warmedUpGroups = [];
    const warmedUpTeachers = [];

    engine.warmupSingleGroup = async (group) => {
        warmedUpGroups.push(group.id);
    };
    engine.warmupSingleTeacher = async (teacher) => {
        warmedUpTeachers.push(teacher.id);
    };

    engine.topGroups = [{ id: 1 }, { id: 2 }, { id: 3 }];
    engine.lastTopGroupsFetchAt = Date.now();

    engine.topTeachers = [{ id: 10 }, { id: 20 }];
    engine.lastTopTeachersFetchAt = Date.now();

    // Force canMakeApiCall to true for test
    const origCanCall = buketovApiService.canMakeApiCall;
    buketovApiService.canMakeApiCall = () => true;

    try {
        // Run 4 group ticks -> should cycle 1, 2, 3, 1
        await engine.tickGroup();
        await engine.tickGroup();
        await engine.tickGroup();
        await engine.tickGroup();
        assert.deepEqual(warmedUpGroups, [1, 2, 3, 1]);

        // Run 3 teacher ticks -> should cycle 10, 20, 10
        await engine.tickTeacher();
        await engine.tickTeacher();
        await engine.tickTeacher();
        assert.deepEqual(warmedUpTeachers, [10, 20, 10]);
    } finally {
        buketovApiService.canMakeApiCall = origCanCall;
    }
});

test('SmartWarmupEngine: top lists cached for 2 hours and not re-queried if cache is fresh', async () => {
    const engine = new SmartWarmupEngine();
    let refreshGroupsCalled = 0;

    engine.refreshTopGroups = async () => {
        refreshGroupsCalled++;
        engine.topGroups = [{ id: 1 }];
        engine.lastTopGroupsFetchAt = Date.now();
    };

    engine.warmupSingleGroup = async () => {};

    const origCanCall = buketovApiService.canMakeApiCall;
    buketovApiService.canMakeApiCall = () => true;

    try {
        // First tick: cache is empty -> refreshTopGroups called once
        await engine.tickGroup();
        assert.equal(refreshGroupsCalled, 1);

        // Second tick: cache is fresh (< 2 hours) -> refreshTopGroups NOT called again
        await engine.tickGroup();
        assert.equal(refreshGroupsCalled, 1);
    } finally {
        buketovApiService.canMakeApiCall = origCanCall;
    }
});

test('SmartWarmupEngine: start() and stop() manage timers cleanly', () => {
    const engine = new SmartWarmupEngine({ groupIntervalMs: 50, teacherIntervalMs: 50 });
    assert.equal(engine.isRunning, false);
    assert.equal(engine.groupTimer, null);
    assert.equal(engine.teacherTimer, null);

    engine.start();
    assert.equal(engine.isRunning, true);
    assert.notEqual(engine.groupTimer, null);
    assert.notEqual(engine.teacherTimer, null);

    engine.stop();
    assert.equal(engine.isRunning, false);
    assert.equal(engine.groupTimer, null);
    assert.equal(engine.teacherTimer, null);
});
