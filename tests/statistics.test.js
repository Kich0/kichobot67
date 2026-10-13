import test from 'node:test';
import assert from 'node:assert/strict';
import userActivityService from '../bot/services/userActivityService.js';
import { UserAction } from '../bot/models/userAction.js';
import { User } from '../bot/models/user.js';

test('statistics: getKZDayInfo calculates correct Asia/Almaty (UTC+5) boundaries and date keys', () => {
    const today = userActivityService.getKZDayInfo(0);
    const yesterday = userActivityService.getKZDayInfo(1);
    const sevenDaysAgo = userActivityService.getKZDayInfo(7);

    // Date keys must match YYYY-MM-DD
    assert.match(today.dateKey, /^\d{4}-\d{2}-\d{2}$/);
    assert.match(yesterday.dateKey, /^\d{4}-\d{2}-\d{2}$/);
    assert.match(sevenDaysAgo.dateKey, /^\d{4}-\d{2}-\d{2}$/);

    // Labels must match DD.MM
    assert.match(today.dayMonth, /^\d{2}\.\d{2}$/);
    assert.match(yesterday.dayMonth, /^\d{2}\.\d{2}$/);
    assert.match(sevenDaysAgo.dayMonth, /^\d{2}\.\d{2}$/);

    // Boundaries span exactly 24 hours minus 1 ms
    assert.equal(today.endUtc.getTime() - today.startUtc.getTime(), 24 * 60 * 60 * 1000 - 1);
    assert.equal(yesterday.endUtc.getTime() - yesterday.startUtc.getTime(), 24 * 60 * 60 * 1000 - 1);

    // Yesterday start must be exactly 24h prior to today start
    assert.equal(today.startUtc.getTime() - yesterday.startUtc.getTime(), 24 * 60 * 60 * 1000);
});

test('statistics: formatStatMessage strictly removes "(время Казахстана UTC+5)" and includes actions, activity, registrations', () => {
    const mockStats = {
        activeUsersToday: 654,
        actionsToday: 4120,
        registeredUsersToday: 12,
        weekUniqueUsersCount: 1850,
        weekTotalActionsCount: 41222,
        weekRegisteredUsersCount: 141,
        dailyActivity: [
            { dateStr: '12.10', count: 1175 },
            { dateStr: '11.10', count: 980 },
            { dateStr: '10.10', count: 840 },
            { dateStr: '09.10', count: 750 },
            { dateStr: '08.10', count: 920 },
            { dateStr: '07.10', count: 810 },
            { dateStr: '06.10', count: 790 }
        ],
        dailyActions: [
            { dateStr: '12.10', count: 8422 },
            { dateStr: '11.10', count: 6540 },
            { dateStr: '10.10', count: 5890 },
            { dateStr: '09.10', count: 4720 },
            { dateStr: '08.10', count: 6100 },
            { dateStr: '07.10', count: 5430 },
            { dateStr: '06.10', count: 4120 }
        ],
        dailyRegistrations: [
            { dateStr: '12.10', count: 33 },
            { dateStr: '11.10', count: 25 },
            { dateStr: '10.10', count: 18 },
            { dateStr: '09.10', count: 14 },
            { dateStr: '08.10', count: 20 },
            { dateStr: '07.10', count: 19 },
            { dateStr: '06.10', count: 12 }
        ]
    };

    const formatted = userActivityService.formatStatMessage(mockStats);

    // 1. Must NOT contain "(время Казахстана UTC+5)"
    assert.ok(!formatted.includes('время Казахстана'));
    assert.ok(!formatted.includes('UTC+5'));

    // 2. Must contain all 3 sections
    assert.ok(formatted.includes('Статистика ежедневного онлайна:'));
    assert.ok(formatted.includes('Статистика ежедневных действий:'));
    assert.ok(formatted.includes('Статистика ежедневных регистраций:'));

    // 3. Must contain "Сегодня" in all 3 sections
    assert.ok(formatted.includes('Сегодня: 654'));
    assert.ok(formatted.includes('Сегодня: 4120'));
    assert.ok(formatted.includes('Сегодня: 12'));

    // 4. Must contain 12.10 data
    assert.ok(formatted.includes('12.10: 1175'));
    assert.ok(formatted.includes('12.10: 8422'));
    assert.ok(formatted.includes('12.10: 33'));

    // 5. Must contain week totals
    assert.ok(formatted.includes('Всего уникальных пользователей за неделю: 1850'));
    assert.ok(formatted.includes('Всего действий за неделю: 41222'));
    assert.ok(formatted.includes('Всего зареганных пользователей за неделю: 141'));
});

test('statistics: getComprehensiveStatistics executes aggregations and maps daily data', async () => {
    const originalActionAgg = UserAction.aggregate;
    const originalUserAgg = User.aggregate;

    const today = userActivityService.getKZDayInfo(0);
    const yesterday = userActivityService.getKZDayInfo(1);

    UserAction.aggregate = async (pipeline) => {
        return [
            {
                _id: today.dateKey,
                actionsCount: 50,
                uniqueUsers: [1, 2, 3]
            },
            {
                _id: yesterday.dateKey,
                actionsCount: 120,
                uniqueUsers: [2, 3, 4, 5]
            }
        ];
    };

    User.aggregate = async (pipeline) => {
        return [
            {
                _id: today.dateKey,
                registeredCount: 5
            },
            {
                _id: yesterday.dateKey,
                registeredCount: 8
            }
        ];
    };

    try {
        const stats = await userActivityService.getComprehensiveStatistics();

        // Check Today metrics
        assert.equal(stats.activeUsersToday, 3);
        assert.equal(stats.actionsToday, 50);
        assert.equal(stats.registeredUsersToday, 5);

        // Check Week totals
        // Unique users: [1, 2, 3] + [2, 3, 4, 5] = 5 unique users (1, 2, 3, 4, 5)
        assert.equal(stats.weekUniqueUsersCount, 5);
        assert.equal(stats.weekTotalActionsCount, 170);
        assert.equal(stats.weekRegisteredUsersCount, 13);

        // Check Daily arrays (7 past days)
        assert.equal(stats.dailyActivity.length, 7);
        assert.equal(stats.dailyActions.length, 7);
        assert.equal(stats.dailyRegistrations.length, 7);

        // First past day is yesterday
        assert.equal(stats.dailyActivity[0].dateStr, yesterday.dayMonth);
        assert.equal(stats.dailyActivity[0].count, 4);

        assert.equal(stats.dailyActions[0].dateStr, yesterday.dayMonth);
        assert.equal(stats.dailyActions[0].count, 120);

        assert.equal(stats.dailyRegistrations[0].dateStr, yesterday.dayMonth);
        assert.equal(stats.dailyRegistrations[0].count, 8);

        // Empty days must safely return 0 without throwing
        assert.equal(stats.dailyActivity[1].count, 0);
        assert.equal(stats.dailyActions[1].count, 0);
        assert.equal(stats.dailyRegistrations[1].count, 0);
    } finally {
        UserAction.aggregate = originalActionAgg;
        User.aggregate = originalUserAgg;
    }
});

test('statistics: getTodayUserCount and getWeakUserCount query UserAction', async () => {
    const originalDistinct = UserAction.distinct;

    UserAction.distinct = async (field, query) => {
        assert.equal(field, 'userId');
        assert.ok(query.createdAt.$gte instanceof Date);
        return [101, 102, 103, 104];
    };

    try {
        const todayCount = await userActivityService.getTodayUserCount();
        assert.equal(todayCount, 4);

        const weekCount = await userActivityService.getWeakUserCount();
        assert.equal(weekCount, 4);
    } finally {
        UserAction.distinct = originalDistinct;
    }
});
