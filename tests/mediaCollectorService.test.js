import test from 'node:test';
import assert from 'node:assert/strict';
import {
    isMediaMessage,
    isTargetDumpChannel,
    forwardMediaToChannel
} from '../bot/services/mediaCollectorService.js';

test('isMediaMessage correctly identifies media vs text messages', () => {
    // Текстовые сообщения — не должны считаться медиа
    assert.equal(isMediaMessage(null), false);
    assert.equal(isMediaMessage({}), false);
    assert.equal(isMediaMessage({ text: 'Привет, как дела?' }), false);
    assert.equal(isMediaMessage({ text: '/start' }), false);
    assert.equal(isMediaMessage({ photo: [] }), false);

    // Медиа-сообщения — должны определяться
    assert.equal(isMediaMessage({ photo: [{ file_id: 'p1' }] }), true);
    assert.equal(isMediaMessage({ video: { file_id: 'v1' } }), true);
    assert.equal(isMediaMessage({ animation: { file_id: 'a1' } }), true);
    assert.equal(isMediaMessage({ document: { file_id: 'd1' } }), true);
    assert.equal(isMediaMessage({ voice: { file_id: 'vo1' } }), true);
    assert.equal(isMediaMessage({ video_note: { file_id: 'vn1' } }), true);
    assert.equal(isMediaMessage({ audio: { file_id: 'au1' } }), true);

    // Фото с текстовой подписью
    assert.equal(isMediaMessage({ photo: [{ file_id: 'p1' }], caption: 'Расписание' }), true);
});

test('isTargetDumpChannel prevents circular forwarding loops', () => {
    const targetChannel = '-1005389521106';

    // Тот же канал в разных форматах (-100, без -100, чистый ID)
    assert.equal(isTargetDumpChannel('-1005389521106', targetChannel), true);
    assert.equal(isTargetDumpChannel('-5389521106', targetChannel), true);
    assert.equal(isTargetDumpChannel('5389521106', targetChannel), true);

    // Другие беседы и группы КарУ
    assert.equal(isTargetDumpChannel('-1003726979205', targetChannel), false);
    assert.equal(isTargetDumpChannel('123456789', targetChannel), false);
});

test('forwardMediaToChannel filters messages and calls bot.forwardMessage for group media', async () => {
    const calls = [];
    const mockBot = {
        forwardMessage: async (targetId, fromChatId, messageId) => {
            calls.push({ action: 'forward', targetId, fromChatId, messageId });
            return { message_id: 999 };
        },
        copyMessage: async (targetId, fromChatId, messageId) => {
            calls.push({ action: 'copy', targetId, fromChatId, messageId });
            return { message_id: 1000 };
        }
    };

    // 1. Игнорирует личные сообщения
    await forwardMediaToChannel({
        chat: { id: 12345, type: 'private' },
        message_id: 1,
        photo: [{ file_id: 'p1' }]
    }, mockBot);
    assert.equal(calls.length, 0);

    // 2. Игнорирует обычный текст из групп
    await forwardMediaToChannel({
        chat: { id: -1003726979205, type: 'supergroup' },
        message_id: 2,
        text: 'завтра отнести'
    }, mockBot);
    assert.equal(calls.length, 0);

    // 3. Игнорирует сообщения из самого целевого канала
    await forwardMediaToChannel({
        chat: { id: -1005389521106, type: 'channel' },
        message_id: 3,
        photo: [{ file_id: 'p1' }]
    }, mockBot);
    assert.equal(calls.length, 0);

    // 4. Пересылает медиа из студенческой группы
    await forwardMediaToChannel({
        chat: { id: -1003726979205, type: 'supergroup' },
        message_id: 42,
        photo: [{ file_id: 'photo_abc' }],
        caption: 'Лекция 1'
    }, mockBot);

    assert.equal(calls.length, 1);
    assert.equal(calls[0].action, 'forward');
    assert.equal(calls[0].fromChatId, -1003726979205);
    assert.equal(calls[0].messageId, 42);
});

test('forwardMediaToChannel falls back to copyMessage if forwarding is restricted', async () => {
    const calls = [];
    const mockBot = {
        forwardMessage: async () => {
            throw new Error('TelegramError: 400 Bad Request: CHAT_FORWARDS_RESTRICTED');
        },
        copyMessage: async (targetId, fromChatId, messageId) => {
            calls.push({ action: 'copy', targetId, fromChatId, messageId });
            return { message_id: 1001 };
        }
    };

    await forwardMediaToChannel({
        chat: { id: -1003726979205, type: 'supergroup' },
        message_id: 77,
        document: { file_id: 'doc_123' }
    }, mockBot);

    assert.equal(calls.length, 1);
    assert.equal(calls[0].action, 'copy');
    assert.equal(calls[0].fromChatId, -1003726979205);
    assert.equal(calls[0].messageId, 77);
});
