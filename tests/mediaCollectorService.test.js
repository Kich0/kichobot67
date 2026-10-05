import test from 'node:test';
import assert from 'node:assert/strict';
import {
    isMediaMessage,
    isTargetDumpChannel,
    extractMediaDetails,
    canDownloadFile,
    recordDownloadedBytes,
    resetDailyDownloadedQuota,
    getDailyDownloadedBytes,
    setDailyDownloadedBytes,
    forwardMediaToChannel,
    MAX_FILE_DOWNLOAD_BYTES,
    MAX_DAILY_DOWNLOAD_BYTES
} from '../bot/services/mediaCollectorService.js';

test('isMediaMessage correctly identifies media vs text messages', () => {
    assert.equal(isMediaMessage(null), false);
    assert.equal(isMediaMessage({}), false);
    assert.equal(isMediaMessage({ text: 'Привет, как дела?' }), false);
    assert.equal(isMediaMessage({ text: '/start' }), false);
    assert.equal(isMediaMessage({ photo: [] }), false);

    assert.equal(isMediaMessage({ photo: [{ file_id: 'p1' }] }), true);
    assert.equal(isMediaMessage({ video: { file_id: 'v1' } }), true);
    assert.equal(isMediaMessage({ animation: { file_id: 'a1' } }), true);
    assert.equal(isMediaMessage({ document: { file_id: 'd1' } }), true);
    assert.equal(isMediaMessage({ voice: { file_id: 'vo1' } }), true);
    assert.equal(isMediaMessage({ video_note: { file_id: 'vn1' } }), true);
    assert.equal(isMediaMessage({ audio: { file_id: 'au1' } }), true);
    assert.equal(isMediaMessage({ photo: [{ file_id: 'p1' }], caption: 'Расписание' }), true);
});

test('isTargetDumpChannel prevents circular forwarding loops', () => {
    const targetChannel = '-1005389521106';

    assert.equal(isTargetDumpChannel('-1005389521106', targetChannel), true);
    assert.equal(isTargetDumpChannel('-5389521106', targetChannel), true);
    assert.equal(isTargetDumpChannel('5389521106', targetChannel), true);

    assert.equal(isTargetDumpChannel('-1003726979205', targetChannel), false);
    assert.equal(isTargetDumpChannel('123456789', targetChannel), false);
});

test('extractMediaDetails correctly extracts media type, fileId, and size', () => {
    const photoMsg = {
        photo: [
            { file_id: 'thumb', file_size: 1000 },
            { file_id: 'orig', file_size: 50000 }
        ]
    };
    const details = extractMediaDetails(photoMsg);
    assert.equal(details.type, 'photo');
    assert.equal(details.fileId, 'orig');
    assert.equal(details.fileSize, 50000);

    const docMsg = {
        document: {
            file_id: 'doc1',
            file_size: 1024,
            file_name: 'test.pdf'
        }
    };
    const docDetails = extractMediaDetails(docMsg);
    assert.equal(docDetails.type, 'document');
    assert.equal(docDetails.fileId, 'doc1');
    assert.equal(docDetails.fileName, 'test.pdf');
});

test('Daily quota and single-file limit (15 MB max per file, 70 MB max per day)', () => {
    resetDailyDownloadedQuota();

    // 1. Файл размером ровно 15 МБ — разрешён
    assert.equal(canDownloadFile(15 * 1024 * 1024), true);

    // 2. Файл больше 15 МБ (например, 16 МБ) — запрещён
    assert.equal(canDownloadFile(16 * 1024 * 1024), false);

    // 3. Записываем скачивание 50 МБ
    recordDownloadedBytes(50 * 1024 * 1024);
    assert.equal(getDailyDownloadedBytes(), 50 * 1024 * 1024);

    // 4. Ещё файл 15 МБ: 50 + 15 = 65 МБ <= 70 МБ — разрешён
    assert.equal(canDownloadFile(15 * 1024 * 1024), true);
    recordDownloadedBytes(15 * 1024 * 1024);
    assert.equal(getDailyDownloadedBytes(), 65 * 1024 * 1024);

    // 5. Попытка скачать ещё 10 МБ: 65 + 10 = 75 МБ > 70 МБ — заблокировано суточным лимитом!
    assert.equal(canDownloadFile(10 * 1024 * 1024), false);

    // 6. Попытка скачать даже 6 МБ: 65 + 6 = 71 МБ > 70 МБ — заблокировано!
    assert.equal(canDownloadFile(6 * 1024 * 1024), false);

    // 7. Попытка скачать 4 МБ: 65 + 4 = 69 МБ <= 70 МБ — разрешено!
    assert.equal(canDownloadFile(4 * 1024 * 1024), true);

    // 8. Сброс суточной квоты (на следующий день) — снова свободно 70 МБ
    resetDailyDownloadedQuota();
    assert.equal(getDailyDownloadedBytes(), 0);
    assert.equal(canDownloadFile(15 * 1024 * 1024), true);
});

test('forwardMediaToChannel falls back to download when forward, copy, and sendFileId fail', async () => {
    resetDailyDownloadedQuota();

    const actions = [];
    const mockBot = {
        forwardMessage: async () => {
            actions.push('forward_failed');
            throw new Error('CHAT_FORWARDS_RESTRICTED');
        },
        copyMessage: async () => {
            actions.push('copy_failed');
            throw new Error('CHAT_FORWARDS_RESTRICTED');
        },
        sendPhoto: async (targetId, fileOrBuffer) => {
            if (Buffer.isBuffer(fileOrBuffer)) {
                actions.push('uploaded_downloaded_buffer');
                return { message_id: 888 };
            }
            actions.push('send_file_id_failed');
            throw new Error('RESTRICTED_FILE_ID');
        },
        getFile: async () => {
            return { file_id: 'p_1', file_size: 2 * 1024 * 1024 };
        },
        getFileLink: async () => {
            actions.push('got_file_link');
            // Data URL с 4 байтами для теста fetch
            return 'data:image/jpeg;base64,/9j/';
        }
    };

    await forwardMediaToChannel({
        chat: { id: -1003726979205, type: 'supergroup' },
        message_id: 99,
        photo: [{ file_id: 'p_1', file_size: 2 * 1024 * 1024 }]
    }, mockBot);

    // Проверяем полную цепочку: forward -> copy -> sendPhoto by file_id -> download fallback -> upload
    assert.ok(actions.includes('forward_failed'));
    assert.ok(actions.includes('copy_failed'));
    assert.ok(actions.includes('send_file_id_failed'));
    assert.ok(actions.includes('got_file_link'));
    assert.ok(actions.includes('uploaded_downloaded_buffer'));
});
