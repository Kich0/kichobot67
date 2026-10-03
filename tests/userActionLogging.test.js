import test from 'node:test';
import assert from 'node:assert/strict';
import ExcelJS from 'exceljs';
import { UserActionService, formatCappedText, deduplicateActions } from '../bot/services/userActionService.js';
import { UserAction } from '../bot/models/userAction.js';
import { styleHeaderRow } from '../scratch/export_database_to_drive.js';

test('formatCappedText: safely handles null, undefined, empty text', () => {
    assert.equal(formatCappedText(null), '');
    assert.equal(formatCappedText(undefined), '');
    assert.equal(formatCappedText(''), '');
    assert.equal(formatCappedText('   '), '');
});

test('formatCappedText: keeps normal text intact', () => {
    const normal = 'Открыл расписание группы ИС-21-1 (Понедельник)';
    assert.equal(formatCappedText(normal), normal);
});

test('formatCappedText: strictly caps text exceeding 50 words', () => {
    const longWords = Array.from({ length: 70 }, (_, i) => `w${i}`).join(' ');
    const capped = formatCappedText(longWords);
    const wordCount = capped.split(/\s+/).length;
    assert.equal(wordCount, 50);
    assert.ok(capped.startsWith('w0 w1'));
    assert.ok(capped.endsWith('w49'));
});

test('formatCappedText: strictly caps text exceeding 250 characters', () => {
    const longString = 'a'.repeat(350);
    const capped = formatCappedText(longString);
    assert.equal(capped.length, 250);
});

test('formatCappedText: handles multi-spaces and newlines properly', () => {
    const textWithSpaces = '  слово1    слово2 \n\n слово3  ';
    const capped = formatCappedText(textWithSpaces);
    assert.equal(capped, 'слово1    слово2 \n\n слово3');
});

test('userActionService: micro-batching queue accumulates and flushes at threshold (>= 25)', async () => {
    const service = new UserActionService();
    // Clear timer to prevent interference during unit test
    if (service.timer) clearInterval(service.timer);

    const insertedBatches = [];
    const originalInsertMany = UserAction.insertMany;
    UserAction.insertMany = async (batch, options) => {
        insertedBatches.push({ batch: [...batch], options });
        return batch;
    };

    try {
        // Add 24 items (less than threshold of 25)
        for (let i = 1; i <= 24; i++) {
            await service.logAction(100 + i, `@user${i}`, 'test_action', `Message ${i}`);
        }

        assert.equal(service.queue.length, 24);
        assert.equal(insertedBatches.length, 0, 'Should not flush before threshold');

        // 25th item triggers immediate auto-flush
        await service.logAction(125, '@user25', 'test_action', 'Message 25');

        assert.equal(service.queue.length, 0, 'Queue should be empty after flush');
        assert.equal(insertedBatches.length, 1);
        assert.equal(insertedBatches[0].batch.length, 25);
        assert.equal(insertedBatches[0].options.ordered, false);
        assert.equal(insertedBatches[0].batch[0].userId, 101);
        assert.equal(insertedBatches[0].batch[0].username, 'user1', 'Leading @ should be stripped');
        assert.ok(insertedBatches[0].batch[0].createdAt instanceof Date);
    } finally {
        UserAction.insertMany = originalInsertMany;
    }
});

test('userActionService: ignores falsy userId', async () => {
    const service = new UserActionService();
    if (service.timer) clearInterval(service.timer);

    await service.logAction(null, 'u', 'act', 'text');
    await service.logAction(0, 'u', 'act', 'text');
    await service.logAction(undefined, 'u', 'act', 'text');

    assert.equal(service.queue.length, 0);
});

test('userActionService: manual flush() flushes remaining items gracefully', async () => {
    const service = new UserActionService();
    if (service.timer) clearInterval(service.timer);

    const insertedBatches = [];
    const originalInsertMany = UserAction.insertMany;
    UserAction.insertMany = async (batch) => {
        insertedBatches.push([...batch]);
        return batch;
    };

    try {
        await service.logAction(999, 'student', 'custom_click', 'Clicked button');
        assert.equal(service.queue.length, 1);

        await service.flush();
        assert.equal(service.queue.length, 0);
        assert.equal(insertedBatches.length, 1);
        assert.equal(insertedBatches[0][0].userId, 999);
        assert.equal(insertedBatches[0][0].text, 'Clicked button');
    } finally {
        UserAction.insertMany = originalInsertMany;
    }
});

test('userActionService: flush failure does not lock isFlushing', async () => {
    const service = new UserActionService();
    if (service.timer) clearInterval(service.timer);

    const originalInsertMany = UserAction.insertMany;
    UserAction.insertMany = async () => {
        throw new Error('Database temporary network failure');
    };

    try {
        await service.logAction(111, 'u', 'act', 'msg');
        await service.flush();
        assert.equal(service.isFlushing, false, 'isFlushing flag must be reset even on error');
    } finally {
        UserAction.insertMany = originalInsertMany;
    }
});

test('userActionService: queue drops oldest items when maxQueueSize is reached', async () => {
    const service = new UserActionService();
    if (service.timer) clearInterval(service.timer);
    service.batchThreshold = 10000; // prevent auto-flush
    service.maxQueueSize = 5;

    for (let i = 1; i <= 6; i++) {
        await service.logAction(i, 'u', 'act', `msg ${i}`);
    }

    assert.equal(service.queue.length, 5);
    // Item 1 should have been dropped, queue starts with item 2
    assert.equal(service.queue[0].userId, 2);
    assert.equal(service.queue[4].userId, 6);
});

test('userActionService: formatDate and formatActionsList work correctly', () => {
    const service = new UserActionService();
    if (service.timer) clearInterval(service.timer);

    assert.equal(service.formatDate(null), '');
    assert.equal(service.formatActionsList([]), '• <i>История действий пуста (пока нет записей)</i>');

    const sampleDate = new Date('2026-10-03T08:30:15Z');
    const formattedDate = service.formatDate(sampleDate);
    assert.ok(formattedDate.includes('2026'));

    const listOutput = service.formatActionsList([
        { createdAt: sampleDate, text: 'Открыл расписание' }
    ]);
    assert.ok(listOutput.includes('Открыл расписание'));
    assert.ok(listOutput.includes('<code>'));
});

test('deduplicateActions: two-tier deduplication and chronological sort', () => {
    const date1 = new Date('2026-10-01T10:00:00Z');
    const date2 = new Date('2026-10-02T10:00:00Z');
    const date3 = new Date('2026-10-03T10:00:00Z');

    const candidates = [
        // Duplicate by _id
        { _id: 'id_1', userId: 1, action: 'view_group', text: 'ИС-21-1', createdAt: date1 },
        { _id: 'id_1', userId: 1, action: 'view_group', text: 'ИС-21-1', createdAt: date1 },

        // Duplicate by composite key (different _id or no _id, same user, timeSec, action, text)
        { _id: 'id_2', userId: 2, action: 'refresh_group', text: 'МАТО-22-2', createdAt: date2 },
        { _id: 'id_3', userId: 2, action: 'refresh_group', text: 'МАТО-22-2', createdAt: date2 },

        // Distinct action
        { _id: 'id_4', userId: 3, action: 'view_teacher', text: 'Жунусова', createdAt: date3 }
    ];

    const { actions, duplicatesSkipped } = deduplicateActions(candidates);

    assert.equal(duplicatesSkipped, 2, 'Should skip 2 duplicates (1 by _id, 1 by composite key)');
    assert.equal(actions.length, 3, 'Should keep 3 unique actions');

    // Chronological order: newest to oldest
    assert.equal(actions[0].userId, 3, 'Newest date3 should be first');
    assert.equal(actions[1].userId, 2, 'Middle date2 should be second');
    assert.equal(actions[2].userId, 1, 'Oldest date1 should be last');
});

test('userActionService: empty/whitespace text falls back to action to satisfy Mongoose schema', async () => {
    const service = new UserActionService();
    service.stop();

    await service.logAction(555, '@tester', 'start_menu', '');
    await service.logAction(556, '@tester', 'chat_input', '   ');

    assert.equal(service.queue.length, 2);
    // Neither text property is empty string
    assert.equal(service.queue[0].text, 'start_menu');
    assert.equal(service.queue[1].text, 'chat_input');

    // Mongoose schema validation passes on both documents
    const doc1 = new UserAction(service.queue[0]);
    const doc2 = new UserAction(service.queue[1]);
    assert.equal(doc1.validateSync(), undefined, 'Doc 1 should pass schema validation');
    assert.equal(doc2.validateSync(), undefined, 'Doc 2 should pass schema validation');
});

test('userActionService: stop() clears interval timer cleanly', () => {
    const service = new UserActionService();
    assert.ok(service.timer !== null);
    service.stop();
    assert.equal(service.timer, null);
});

test('deduplicateActions: handles missing or invalid createdAt dates without NaN sort errors', () => {
    const candidates = [
        { _id: '1', userId: 1, action: 'act', text: 'txt', createdAt: 'invalid-date' },
        { _id: '2', userId: 2, action: 'act', text: 'txt', createdAt: null },
        { _id: '3', userId: 3, action: 'act', text: 'txt', createdAt: new Date('2026-10-03T12:00:00Z') },
        { _id: '4', userId: 4, action: 'act', text: 'txt', createdAt: new Date('2026-10-02T12:00:00Z') }
    ];

    const { actions } = deduplicateActions(candidates);
    assert.equal(actions.length, 4);
    assert.equal(actions[0].userId, 3, 'Valid latest date must be sorted first');
    assert.equal(actions[1].userId, 4, 'Valid earlier date must be sorted second');
});

test('styleHeaderRow: applies frozen view and autoFilter across columns in ExcelJS', () => {
    const wb = new ExcelJS.Workbook();
    const sheet = wb.addWorksheet('TestSheet');
    sheet.columns = [
        { header: 'Col 1', key: 'c1', width: 20 },
        { header: 'Col 2', key: 'c2', width: 30 }
    ];

    styleHeaderRow(sheet);

    // Verify frozen header row
    assert.deepEqual(sheet.views, [
        { state: 'frozen', xSplit: 0, ySplit: 1, activeCell: 'A2' }
    ]);

    // Verify autoFilter spans from column 1 to column 2
    assert.deepEqual(sheet.autoFilter, {
        from: { row: 1, column: 1 },
        to: { row: 1, column: 2 }
    });

    // Verify header styling
    const headerRow = sheet.getRow(1);
    assert.equal(headerRow.font.bold, true);
    assert.equal(headerRow.height, 25);
});
