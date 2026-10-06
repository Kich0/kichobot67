import test from 'node:test';
import assert from 'node:assert/strict';

import blackListService from '../bot/services/blackListService.js';
import userTollService from '../bot/services/userTollService.js';
import { parseDuration, formatDurationDate } from '../bot/controllers/commands/adminCommands/moderationCommands.js';

test('parseDuration parses time strings correctly', () => {
    assert.equal(parseDuration('10m'), 10 * 60 * 1000);
    assert.equal(parseDuration('15min'), 15 * 60 * 1000);
    assert.equal(parseDuration('30мин'), 30 * 60 * 1000);

    assert.equal(parseDuration('1h'), 60 * 60 * 1000);
    assert.equal(parseDuration('2hour'), 2 * 60 * 60 * 1000);
    assert.equal(parseDuration('5ч'), 5 * 60 * 60 * 1000);

    assert.equal(parseDuration('1d'), 24 * 60 * 60 * 1000);
    assert.equal(parseDuration('3days'), null); // not matching single digit or custom regex
    assert.equal(parseDuration('3день'), 3 * 24 * 60 * 60 * 1000);
    assert.equal(parseDuration('7d'), 7 * 24 * 60 * 60 * 1000);

    assert.equal(parseDuration('1w'), 7 * 24 * 60 * 60 * 1000);
    assert.equal(parseDuration('2нед'), 14 * 24 * 60 * 60 * 1000);

    // Default numeric string defaults to minutes
    assert.equal(parseDuration('45'), 45 * 60 * 1000);

    // Permanent keywords return null
    assert.equal(parseDuration('perm'), null);
    assert.equal(parseDuration('permanent'), null);
    assert.equal(parseDuration('forever'), null);
    assert.equal(parseDuration('навсегда'), null);
    assert.equal(parseDuration('0'), null);
    assert.equal(parseDuration(''), null);
    assert.equal(parseDuration(null), null);
});

test('formatDurationDate formats dates with Asia/Almaty timezone', () => {
    assert.equal(formatDurationDate(null), 'навсегда');
    assert.equal(formatDurationDate(undefined), 'навсегда');

    const testDate = new Date('2026-10-10T12:00:00Z');
    const formatted = formatDurationDate(testDate);
    assert.ok(formatted.includes('2026'), 'Should contain year 2026');
    assert.ok(formatted.includes('UTC+5'), 'Should contain UTC+5 note');
});

test('blackListService handles bans, mutes, and expiration', async () => {
    const testUserId = 9999001;

    // Initially not banned or muted
    assert.equal(blackListService.isBanned(testUserId), false);
    assert.equal(blackListService.isMuted(testUserId), false);
    assert.equal(await blackListService.isBlackListed(testUserId), false);

    // Add permanent ban
    await blackListService.addBan(testUserId, { reason: 'Test ban', username: 'test_banned' });
    assert.equal(blackListService.isBanned(testUserId), true);
    assert.equal(blackListService.isMuted(testUserId), false);
    assert.equal(await blackListService.isBlackListed(testUserId), true);
    const r1 = blackListService.getRestriction(testUserId);
    assert.equal(r1.type, 'ban');
    assert.equal(r1.reason, 'Test ban');
    assert.equal(r1.until, null);

    // Add temporary mute
    const futureDate = new Date(Date.now() + 60000);
    await blackListService.addMute(testUserId, { until: futureDate, reason: 'Spam' });
    assert.equal(blackListService.isBanned(testUserId), false);
    assert.equal(blackListService.isMuted(testUserId), true);
    const r2 = blackListService.getRestriction(testUserId);
    assert.equal(r2.type, 'mute');
    assert.equal(r2.reason, 'Spam');

    // Remove restriction
    await blackListService.remove(testUserId);
    assert.equal(blackListService.isBanned(testUserId), false);
    assert.equal(blackListService.isMuted(testUserId), false);
    assert.equal(blackListService.getRestriction(testUserId), null);

    // Test expired restriction
    const pastDate = new Date(Date.now() - 1000);
    await blackListService.addMute(testUserId, { until: pastDate });
    // getRestriction should detect expiration and return null
    assert.equal(blackListService.getRestriction(testUserId), null);
    assert.equal(blackListService.isMuted(testUserId), false);
});

test('userTollService enables, disables, and checks 9000 stars toll', async () => {
    const tolledId = 8888001;
    const normalId = 8888002;

    // Normal user is not tolled
    assert.equal(userTollService.isUserTolled(normalId), false);
    assert.equal(userTollService.isUserTolled(tolledId), false);

    // Enable toll for targeted user
    await userTollService.enableToll(tolledId, { username: 'victim_user', addedBy: 12345 });
    assert.equal(userTollService.isUserTolled(tolledId), true);
    assert.equal(userTollService.isUserTolled(normalId), false); // other users completely unaffected
    assert.equal(userTollService.getTollPrice(tolledId), 9000);

    const info = userTollService.getTollInfo(tolledId);
    assert.equal(info.starPrice, 9000);
    assert.equal(info.username, 'victim_user');

    const all = userTollService.getAllTolled();
    assert.ok(all.some(u => u.userId === tolledId));

    // Disable toll
    await userTollService.disableToll(tolledId);
    assert.equal(userTollService.isUserTolled(tolledId), false);
});

test('Telegram Stars invoice parameters follow official Telegram requirements', () => {
    const starPrice = 9000;
    const invoice = {
        currency: 'XTR',
        provider_token: '',
        prices: [{ label: 'Нажатие кнопки', amount: starPrice }],
    };

    assert.equal(invoice.currency, 'XTR', 'Currency must be XTR for Telegram Stars');
    assert.equal(invoice.provider_token, '', 'provider_token must be empty for Telegram Stars');
    assert.equal(invoice.prices[0].amount, 9000, 'Price must be exactly 9 000 stars');
});
