import log from "../../logging/logging.js";

const MAX_TEXT_LENGTH = 50;
const RATE_LIMIT_MS = 1500;
const SPAM_WINDOW_MS = 10000;
const SPAM_MAX_MESSAGES = 8;
const BAN_DURATION_MS = 60000;
const WARNING_COOLDOWN_MS = 5000;

const userGateTimestamps = {};
const userMessageCounts = {};
const bannedUsers = {};
const gateWarnings = {};
const blockedMessages = new Set();

setInterval(() => {
    const now = Date.now();
    for (const uid in userMessageCounts) {
        userMessageCounts[uid] = userMessageCounts[uid].filter(t => now - t < SPAM_WINDOW_MS);
        if (!userMessageCounts[uid].length) delete userMessageCounts[uid];
    }
    for (const uid in bannedUsers) {
        if (now > bannedUsers[uid]) delete bannedUsers[uid];
    }
    for (const uid in gateWarnings) {
        if (now - gateWarnings[uid] > 30000) delete gateWarnings[uid];
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

function sendWarning(bot, userId, text, now) {
    if (!gateWarnings[userId] || (now - gateWarnings[userId] > WARNING_COOLDOWN_MS)) {
        gateWarnings[userId] = now;
        bot.sendMessage(userId, text).catch(e => {
            log.error(`[GATE] Warning send failed for ${userId}: ${e.message}`);
        });
    }
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

    if (isUserBanned(userId)) {
        blockedMessages.add(key);
        return;
    }

    if (!userMessageCounts[userId]) userMessageCounts[userId] = [];
    userMessageCounts[userId].push(now);
    userMessageCounts[userId] = userMessageCounts[userId].filter(t => now - t < SPAM_WINDOW_MS);

    if (userMessageCounts[userId].length > SPAM_MAX_MESSAGES) {
        bannedUsers[userId] = now + BAN_DURATION_MS;
        blockedMessages.add(key);
        log.warn(`[GATE] User ${userId} BANNED for ${BAN_DURATION_MS / 1000}s — ${userMessageCounts[userId].length} msgs in ${SPAM_WINDOW_MS / 1000}s`);
        sendWarning(bot, userId, `🚫 Вы заблокированы на ${BAN_DURATION_MS / 1000} секунд за спам.`, now);
        return;
    }

    if (userGateTimestamps[userId] && (now - userGateTimestamps[userId] < RATE_LIMIT_MS)) {
        blockedMessages.add(key);
        return;
    }
    userGateTimestamps[userId] = now;

    if (!msg.text) {
        blockedMessages.add(key);
        sendWarning(bot, userId, "⛔ Бот принимает только текстовые сообщения.\nНажмите /start для начала.", now);
        return;
    }

    if (msg.text.length > MAX_TEXT_LENGTH && !isKnownCommand(msg.text)) {
        blockedMessages.add(key);
        sendWarning(bot, userId, `⛔ Максимум ${MAX_TEXT_LENGTH} символов. Используйте кнопки 👇`, now);
        return;
    }
}
