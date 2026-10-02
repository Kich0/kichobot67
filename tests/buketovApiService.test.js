import test from 'node:test';
import assert from 'node:assert/strict';
import BuketovApiService from '../bot/services/buketovApiService.js';

test('BuketovApiService initializes partitioned student and teacher caches with proper capacities', () => {
    assert.equal(BuketovApiService.STUDENT_CAPACITY, 400);
    assert.equal(BuketovApiService.TEACHER_CAPACITY, 100);
    assert.ok(BuketovApiService.studentCache instanceof Map);
    assert.ok(BuketovApiService.teacherCache instanceof Map);
    assert.equal(BuketovApiService.REQUEST_TIMEOUT_MS, 4500);
});

test('BuketovApiService LRU eviction isolates studentCache from teacherCache', () => {
    // Fill teacherCache with an entry
    BuketovApiService._setCache(BuketovApiService.teacherCache, BuketovApiService.TEACHER_CAPACITY, 'teacher_key_1', {
        data: 'teacher_data_1',
        timestamp: Date.now()
    });
    assert.equal(BuketovApiService.teacherCache.size, 1);

    // Now insert entries into studentCache exceeding small test capacity
    const testCapacity = 5;
    for (let i = 1; i <= 6; i++) {
        BuketovApiService._setCache(BuketovApiService.studentCache, testCapacity, `student_key_${i}`, {
            data: `student_data_${i}`,
            timestamp: Date.now()
        });
    }

    // student_key_1 should be evicted because capacity is 5 and 6 were added
    assert.equal(BuketovApiService.studentCache.has('student_key_1'), false);
    assert.equal(BuketovApiService.studentCache.has('student_key_6'), true);
    assert.equal(BuketovApiService.studentCache.size, testCapacity);

    // teacherCache must remain completely unaffected!
    assert.equal(BuketovApiService.teacherCache.has('teacher_key_1'), true);
    assert.equal(BuketovApiService.teacherCache.size, 1);

    // Clean up
    BuketovApiService.studentCache.clear();
    BuketovApiService.teacherCache.clear();
});

test('BuketovApiService Circuit Breaker transitions: CLOSED -> API_DOWN -> HALF_OPEN -> CLOSED', () => {
    // 1. Initial state
    BuketovApiService.circuitState = 'CLOSED';
    BuketovApiService.consecutiveFailures = 0;
    BuketovApiService.circuitTrippedAt = 0;

    // 2. First failure: remains CLOSED
    BuketovApiService._recordFailure(new Error('Network error 1'));
    assert.equal(BuketovApiService.consecutiveFailures, 1);
    assert.equal(BuketovApiService.circuitState, 'CLOSED');

    // 3. Second failure: trips to API_DOWN
    BuketovApiService._recordFailure(new Error('Network error 2'));
    assert.equal(BuketovApiService.consecutiveFailures, 2);
    assert.equal(BuketovApiService.circuitState, 'API_DOWN');
    assert.ok(BuketovApiService.circuitTrippedAt > 0);

    // 4. Recovery resets state to CLOSED
    BuketovApiService._recordSuccess();
    assert.equal(BuketovApiService.consecutiveFailures, 0);
    assert.equal(BuketovApiService.circuitState, 'CLOSED');
});

test('BuketovApiService Circuit Breaker trips back to API_DOWN if HALF_OPEN probe fails', () => {
    // Put into HALF_OPEN state
    BuketovApiService.circuitState = 'HALF_OPEN';
    BuketovApiService.consecutiveFailures = 0;

    // Probe failure should immediately trip to API_DOWN
    BuketovApiService._recordFailure(new Error('Probe failed'));
    assert.equal(BuketovApiService.circuitState, 'API_DOWN');
    assert.equal(BuketovApiService.consecutiveFailures, BuketovApiService.FAILURE_THRESHOLD);

    // Reset
    BuketovApiService._recordSuccess();
});

test('BuketovApiService fetchSchedule throws 503 circuitError immediately during API_DOWN', async () => {
    BuketovApiService.circuitState = 'API_DOWN';
    BuketovApiService.circuitTrippedAt = Date.now(); // tripped just now (cooldown active)
    BuketovApiService.studentCache.clear();

    await assert.rejects(
        async () => {
            await BuketovApiService.fetchSchedule({ group: 'NON_EXISTENT_GROUP_TEST' }, 'student');
        },
        (err) => {
            assert.equal(err.status, 503);
            assert.equal(err.isCircuitBreaker, true);
            return true;
        }
    );

    // Clean up
    BuketovApiService._recordSuccess();
});
