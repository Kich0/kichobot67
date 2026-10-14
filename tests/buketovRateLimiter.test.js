import test from 'node:test';
import assert from 'node:assert/strict';
import BuketovApiService from '../bot/services/buketovApiService.js';

test('BuketovApiService rate limiter initializes MAX_REQUESTS_PER_MINUTE = 110 and empty timestamps array', () => {
    assert.equal(BuketovApiService.MAX_REQUESTS_PER_MINUTE, 110);
    assert.ok(Array.isArray(BuketovApiService.outboundRequestTimestamps));
});

test('checkRateLimit: returns true when fewer than 110 requests exist in the last 60 seconds', () => {
    BuketovApiService.outboundRequestTimestamps = [];
    assert.equal(BuketovApiService.checkRateLimit(), true);

    const now = Date.now();
    for (let i = 0; i < 109; i++) {
        BuketovApiService.outboundRequestTimestamps.push(now - i * 100);
    }
    assert.equal(BuketovApiService.outboundRequestTimestamps.length, 109);
    assert.equal(BuketovApiService.checkRateLimit(), true);

    BuketovApiService.outboundRequestTimestamps = [];
});

test('checkRateLimit: returns false when 110 or more requests exist in the last 60 seconds', () => {
    BuketovApiService.outboundRequestTimestamps = [];
    const now = Date.now();
    for (let i = 0; i < 110; i++) {
        BuketovApiService.outboundRequestTimestamps.push(now - i * 100);
    }
    assert.equal(BuketovApiService.outboundRequestTimestamps.length, 110);
    assert.equal(BuketovApiService.checkRateLimit(), false);

    BuketovApiService.outboundRequestTimestamps = [];
});

test('checkRateLimit: automatically prunes timestamps older than 60,000 ms', () => {
    BuketovApiService.outboundRequestTimestamps = [];
    const now = Date.now();
    // 110 expired timestamps (older than 60 seconds)
    for (let i = 0; i < 110; i++) {
        BuketovApiService.outboundRequestTimestamps.push(now - 65000 - i * 100);
    }
    assert.equal(BuketovApiService.outboundRequestTimestamps.length, 110);

    // checkRateLimit should prune all of them and return true
    const allowed = BuketovApiService.checkRateLimit();
    assert.equal(allowed, true);
    assert.equal(BuketovApiService.outboundRequestTimestamps.length, 0);

    BuketovApiService.outboundRequestTimestamps = [];
});

test('canMakeApiCall: returns true only when rate limit is available AND Circuit Breaker is not API_DOWN', () => {
    BuketovApiService.outboundRequestTimestamps = [];
    BuketovApiService.circuitState = 'CLOSED';

    // 1. Normal state: allowed
    assert.equal(BuketovApiService.canMakeApiCall(), true);

    // 2. Circuit Breaker tripped: blocked
    BuketovApiService.circuitState = 'API_DOWN';
    assert.equal(BuketovApiService.canMakeApiCall(), false);

    // 3. Circuit Breaker recovered, but rate limit full: blocked
    BuketovApiService.circuitState = 'CLOSED';
    const now = Date.now();
    for (let i = 0; i < 110; i++) {
        BuketovApiService.outboundRequestTimestamps.push(now);
    }
    assert.equal(BuketovApiService.canMakeApiCall(), false);

    // Reset
    BuketovApiService.outboundRequestTimestamps = [];
    BuketovApiService.circuitState = 'CLOSED';
});

test('_dbScheduleToRecords: converts MongoDB student schedule days into valid API record objects', () => {
    const days = [
        {
            day: 'Понедельник',
            subjects: [
                {
                    time: '08:30-09:50',
                    subject: 'Математический анализ /Лекция/\nИванов И. И.\nАуд.204/1'
                },
                {
                    time: '10:00-11:20',
                    subject: ''
                }
            ]
        },
        {
            day: 'Сейсенбі',
            subjects: [
                {
                    time: '11:30-12:50',
                    subject: 'Программирование\nПетров П. П.\nАуд.101'
                }
            ]
        }
    ];

    const records = BuketovApiService._dbScheduleToRecords(days, 'ИС-21-1');
    assert.equal(records.length, 2);

    assert.equal(records[0].day, 'Понедельник');
    assert.equal(records[0].dayIndex, 1);
    assert.equal(records[0].slot, 1);
    assert.equal(records[0].time, '08:30-09:50');
    assert.equal(records[0].discipline, 'Математический анализ /Лекция/');
    assert.equal(records[0].teacher, 'Иванов И. И.');
    assert.equal(records[0].room, 'Ауд.204/1');
    assert.equal(records[0].group, 'ИС-21-1');

    assert.equal(records[1].day, 'Сейсенбі');
    assert.equal(records[1].dayIndex, 2);
    assert.equal(records[1].slot, 1);
    assert.equal(records[1].time, '11:30-12:50');
    assert.equal(records[1].discipline, 'Программирование');
    assert.equal(records[1].teacher, 'Петров П. П.');
    assert.equal(records[1].room, 'Ауд.101');
    assert.equal(records[1].group, 'ИС-21-1');
});

test('_dbTeacherScheduleToRecords: converts MongoDB teacher schedule days into valid API record objects', () => {
    const days = [
        {
            day: 'Среда',
            groups: [
                {
                    time: '08:30-09:50',
                    group: 'ФК-11',
                    subject: 'Физическая культура',
                    lessonType: 'Прак.зан.'
                }
            ]
        }
    ];

    const records = BuketovApiService._dbTeacherScheduleToRecords(days, 'Сидоров С. С.');
    assert.equal(records.length, 1);
    assert.equal(records[0].day, 'Среда');
    assert.equal(records[0].dayIndex, 3);
    assert.equal(records[0].slot, 1);
    assert.equal(records[0].time, '08:30-09:50');
    assert.equal(records[0].discipline, 'Физическая культура');
    assert.equal(records[0].teacher, 'Сидоров С. С.');
    assert.equal(records[0].group, 'ФК-11');
    assert.equal(records[0].lessonType, 'Прак.зан.');
});

test('fetchScheduleFromApi: enforces 110 req/min, calls fetchScheduleFromDb and makes 0 network calls when limit reached', async () => {
    // Fill rate limiter with 110 timestamps
    BuketovApiService.outboundRequestTimestamps = [];
    const now = Date.now();
    for (let i = 0; i < 110; i++) {
        BuketovApiService.outboundRequestTimestamps.push(now - 100);
    }
    BuketovApiService.studentCache.clear();

    // Mock fetchScheduleFromDb to test interception
    const originalFetchFromDb = BuketovApiService.fetchScheduleFromDb;
    let fallbackCalled = false;
    let fallbackArgs = null;

    BuketovApiService.fetchScheduleFromDb = async function(params, cacheType) {
        fallbackCalled = true;
        fallbackArgs = { params, cacheType };
        return { records: [{ discipline: 'Mocked From DB' }], total: 1, _fromDb: true };
    };

    try {
        const res = await BuketovApiService.fetchScheduleFromApi({ group: 'TEST-LIMIT-GROUP' }, 'student');

        assert.equal(fallbackCalled, true);
        assert.equal(fallbackArgs.params.group, 'TEST-LIMIT-GROUP');
        assert.equal(fallbackArgs.cacheType, 'student');
        assert.equal(res._fromDb, true);
        assert.equal(res.records[0].discipline, 'Mocked From DB');

        // Verify that NO additional timestamp was added to outboundRequestTimestamps
        assert.equal(BuketovApiService.outboundRequestTimestamps.length, 110);
    } finally {
        BuketovApiService.fetchScheduleFromDb = originalFetchFromDb;
        BuketovApiService.outboundRequestTimestamps = [];
        BuketovApiService.studentCache.clear();
    }
});
