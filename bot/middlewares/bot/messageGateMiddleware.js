import log from "../../logging/logging.js";

const MAX_TEXT_LENGTH = 50;
const RATE_LIMIT_MS = 1500;
const SPAM_WINDOW_MS = 10000;
const SPAM_MAX_MESSAGES = 8;
const BAN_DURATION_MS = 60000;

const userGateTimestamps = {};
const userMessageCounts = {};
const bannedUsers = {};
const banWarningSent = {};
const blockedMessages = new Set();

setInterval(() => {
    const now = Date.now();
    for (const uid in userMessageCounts) {
        userMessageCounts[uid] = userMessageCounts[uid].filter(t => now - t < SPAM_WINDOW_MS);
        if (!userMessageCounts[uid].length) delete userMessageCounts[uid];
    }
    for (const uid in bannedUsers) {
        if (now > bannedUsers[uid]) {
            delete bannedUsers[uid];
            delete banWarningSent[uid];
        }
    }
    for (const uid in userGateTimestamps) {
        if (now - userGateTimestamps[uid] > 60000) delete userGateTimestamps[uid];
    }
    blockedMessages.clear();
}, 3 * 60 * 1000);

const KNOWN_PREFIXES = [
    '🗒', '🗓', '💡',
    'Г ', 'Т ', 'П ', 'О ',
    'Группа', 'Тобы', 'Преподаватель', 'Оқытушы',
    'Поиск', 'расписание', 'профиль',
    'сикс', 'север', 'севен', 'six', 'seven', '67', 'шестьдесят семь'
];

function isKnownCommand(text) {
    if (text.startsWith('/')) return true;
    const lower = text.toLowerCase();
    return KNOWN_PREFIXES.some(p => text.startsWith(p) || lower.startsWith(p.toLowerCase()));
}

export function isUserBanned(userId) {
    return !!(bannedUsers[userId] && Date.now() < bannedUsers[userId]);
}

export function isMessageBlocked(msg) {
    if (!msg) return true;
    const key = `${msg.chat?.id}_${msg.message_id}`;
    return blockedMessages.has(key);
}

export function processMessageGate(msg, bot) {
    if (!msg?.chat?.id) return;

    const userId = msg.chat.id;
    const now = Date.now();
    const key = `${userId}_${msg.message_id}`;

    if (msg.chat.type !== 'private') return;

    // --- Бан: полная тишина, предупреждение отправлено при бане ---
    if (isUserBanned(userId)) {
        blockedMessages.add(key);
        return;
    }

    // --- Считаем ВСЕ сообщения в счётчик спама (до рейт-лимита!) ---
    if (!userMessageCounts[userId]) userMessageCounts[userId] = [];
    userMessageCounts[userId].push(now);
    userMessageCounts[userId] = userMessageCounts[userId].filter(t => now - t < SPAM_WINDOW_MS);

    // --- Проверка на бан за спам ---
    if (userMessageCounts[userId].length > SPAM_MAX_MESSAGES) {
        bannedUsers[userId] = now + BAN_DURATION_MS;
        blockedMessages.add(key);
        const banSec = Math.ceil(BAN_DURATION_MS / 1000);
        log.warn(`[GATE] User ${userId} BANNED for ${banSec}s — ${userMessageCounts[userId].length} msgs in ${SPAM_WINDOW_MS / 1000}s`);

        if (!banWarningSent[userId]) {
            banWarningSent[userId] = true;
            bot.sendMessage(userId,
                `🚫 <b>Блокировка на ${banSec} секунд</b>\n\n` +
                `Вы отправили слишком много сообщений.\n` +
                `⏳ Бот будет игнорировать вас до окончания блокировки.\n\n` +
                `<i>Пожалуйста, не спамьте.</i>`,
                { parse_mode: "HTML" }
            ).catch(e => {
                log.error(`[GATE] Ban warning send failed for ${userId}: ${e.message}`);
            });
        }
        return;
    }

    // --- Рейт-лимит (1 сообщение в 1.5с) — молча блокирует ---
    if (userGateTimestamps[userId] && (now - userGateTimestamps[userId] < RATE_LIMIT_MS)) {
        blockedMessages.add(key);
        return;
    }
    userGateTimestamps[userId] = now;

    // --- Не-текст: стикеры, фото, видео, файлы, голосовые и т.д. ---
    if (!msg.text) {
        blockedMessages.add(key);
        bot.sendMessage(userId,
            `⛔ <b>Бот принимает только текст</b>\n\n` +
            `Стикеры, фото, видео, файлы и голосовые\nне поддерживаются.\n\n` +
            `Нажмите /start для начала.`,
            { parse_mode: "HTML" }
        ).catch(e => {
            log.error(`[GATE] Non-text warning send failed for ${userId}: ${e.message}`);
        });
        return;
    }

    // --- Слишком длинный текст ---
    if (msg.text.length > MAX_TEXT_LENGTH && !isKnownCommand(msg.text)) {
        blockedMessages.add(key);
        bot.sendMessage(userId,
            `⛔ <b>Слишком длинное сообщение</b>\n\n` +
            `Максимум ${MAX_TEXT_LENGTH} символов.\nИспользуйте кнопки 👇`,
            { parse_mode: "HTML" }
        ).catch(e => {
            log.error(`[GATE] Long text warning send failed for ${userId}: ${e.message}`);
        });
        return;
    }
}
