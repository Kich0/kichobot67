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
    sendSpecialContent,
    MAX_FILE_DOWNLOAD_BYTES,
    MAX_DAILY_DOWNLOAD_BYTES
} from '../bot/services/mediaCollectorService.js';

test('isMediaMessage correctly identifies media vs text messages', () => {
    assert.equal(isMediaMessage(null), false);
    assert.equal(isMediaMessage({}), false);
    assert.equal(isMediaMessage({ text: 'Привет, как дела?' }), false);
    assert.equal(isMediaMessage({ text: '/start' }), false);
    assert.equal(isMediaMessage({ photo: [] }), false);

    // Медиа, файлы, анимации, стикеры
    assert.equal(isMediaMessage({ photo: [{ file_id: 'p1' }] }), true);
    assert.equal(isMediaMessage({ video: { file_id: 'v1' } }), true);
    assert.equal(isMediaMessage({ animation: { file_id: 'a1' } }), true);
    assert.equal(isMediaMessage({ sticker: { file_id: 's1' } }), true);
    assert.equal(isMediaMessage({ document: { file_id: 'd1' } }), true);
    assert.equal(isMediaMessage({ voice: { file_id: 'vo1' } }), true);
    assert.equal(isMediaMessage({ video_note: { file_id: 'vn1' } }), true);
    assert.equal(isMediaMessage({ audio: { file_id: 'au1' } }), true);
    assert.equal(isMediaMessage({ contact: { phone_number: '+77777777777' } }), true);
    assert.equal(isMediaMessage({ location: { latitude: 50.1, longitude: 73.1 } }), true);
    assert.equal(isMediaMessage({ poll: { id: 'poll_1' } }), true);
    assert.equal(isMediaMessage({ dice: { value: 6 } }), true);
    assert.equal(isMediaMessage({ photo: [{ file_id: 'p1' }], caption: 'Расписание' }), true);
});

test('isTargetDumpChannel prevents circular forwarding loops', () => {
    const targetChannel = '-1004486026758';

    assert.equal(isTargetDumpChannel('-1004486026758', targetChannel), true);
    assert.equal(isTargetDumpChannel('1004486026758', targetChannel), true);
    assert.equal(isTargetDumpChannel('-5389521106', '-5389521106'), true);

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

    // GIF / animation
    const gifMsg = {
        animation: {
            file_id: 'gif_1',
            file_size: 150000,
            file_name: 'funny.mp4'
        }
    };
    const gifDetails = extractMediaDetails(gifMsg);
    assert.equal(gifDetails.type, 'animation');
    assert.equal(gifDetails.fileId, 'gif_1');

    // Sticker
    const stickerMsg = {
        sticker: {
            file_id: 'stk_1',
            file_size: 20000,
            is_animated: true
        }
    };
    const stickerDetails = extractMediaDetails(stickerMsg);
    assert.equal(stickerDetails.type, 'sticker');
    assert.equal(stickerDetails.fileId, 'stk_1');
    assert.equal(stickerDetails.fileName, 'sticker.tgs');

    // Document
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

    assert.equal(canDownloadFile(15 * 1024 * 1024), true);
    assert.equal(canDownloadFile(16 * 1024 * 1024), false);

    recordDownloadedBytes(50 * 1024 * 1024);
    assert.equal(getDailyDownloadedBytes(), 50 * 1024 * 1024);

    assert.equal(canDownloadFile(15 * 1024 * 1024), true);
    recordDownloadedBytes(15 * 1024 * 1024);
    assert.equal(getDailyDownloadedBytes(), 65 * 1024 * 1024);

    assert.equal(canDownloadFile(10 * 1024 * 1024), false);
    assert.equal(canDownloadFile(6 * 1024 * 1024), false);
    assert.equal(canDownloadFile(4 * 1024 * 1024), true);

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
        sendDocument: async (targetId, fileOrBuffer) => {
            if (Buffer.isBuffer(fileOrBuffer)) {
                actions.push('uploaded_downloaded_buffer');
                return { message_id: 888 };
            }
            actions.push('send_document_file_id_failed');
            throw new Error('RESTRICTED_DOCUMENT_FILE_ID');
        },
        getFile: async () => {
            return { file_id: 'p_1', file_size: 2 * 1024 * 1024 };
        },
        getFileLink: async () => {
            actions.push('got_file_link');
            return 'data:image/jpeg;base64,/9j/';
        }
    };

    await forwardMediaToChannel({
        chat: { id: -1003726979205, type: 'supergroup' },
        message_id: 99,
        photo: [{ file_id: 'p_1', file_size: 2 * 1024 * 1024 }]
    }, mockBot);

    assert.ok(actions.includes('forward_failed'));
    assert.ok(actions.includes('copy_failed'));
    assert.ok(actions.includes('send_file_id_failed'));
    assert.ok(actions.includes('got_file_link'));
    assert.ok(actions.includes('uploaded_downloaded_buffer'));
});

test('forwardMediaToChannel falls back to sendDocument when sendAnimation fails', async () => {
    const actions = [];
    const mockBot = {
        forwardMessage: async () => { throw new Error('CHAT_FORWARDS_RESTRICTED'); },
        copyMessage: async () => { throw new Error('CHAT_FORWARDS_RESTRICTED'); },
        sendAnimation: async () => {
            actions.push('sendAnimation_failed');
            throw new Error('BAD_ANIMATION');
        },
        sendDocument: async (targetId, fileId) => {
            actions.push(`sendDocument_fallback_${fileId}`);
            return { message_id: 999 };
        }
    };

    await forwardMediaToChannel({
        chat: { id: -1003726979205, type: 'supergroup' },
        message_id: 101,
        animation: { file_id: 'anim_test_id', file_size: 50000 }
    }, mockBot);

    assert.ok(actions.includes('sendAnimation_failed'));
    assert.ok(actions.includes('sendDocument_fallback_anim_test_id'));
});

test('sendSpecialContent supports contact, poll, location, dice without files', async () => {
    const results = [];
    const mockBot = {
        sendContact: async (target, phone, first) => {
            results.push(`contact_${phone}_${first}`);
            return { message_id: 1 };
        },
        sendLocation: async (target, lat, lon) => {
            results.push(`location_${lat}_${lon}`);
            return { message_id: 2 };
        },
        sendPoll: async (target, question, opts) => {
            results.push(`poll_${question}_${opts.length}`);
            return { message_id: 3 };
        },
        sendDice: async (target, opts) => {
            results.push(`dice_${opts.emoji}`);
            return { message_id: 4 };
        }
    };

    await sendSpecialContent('-1004486026758', { contact: { phone_number: '+777', first_name: 'Test' } }, mockBot);
    await sendSpecialContent('-1004486026758', { location: { latitude: 49.8, longitude: 73.0 } }, mockBot);
    await sendSpecialContent('-1004486026758', { poll: { question: 'Q1', options: [{ text: 'A' }, { text: 'B' }] } }, mockBot);
    await sendSpecialContent('-1004486026758', { dice: { emoji: '🎲' } }, mockBot);

    assert.deepEqual(results, [
        'contact_+777_Test',
        'location_49.8_73',
        'poll_Q1_2',
        'dice_🎲'
    ]);
});

