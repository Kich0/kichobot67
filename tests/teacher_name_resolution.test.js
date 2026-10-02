import test from 'node:test';
import assert from 'node:assert/strict';
import teacherDirectoryService, {
    normalizeCyrillic,
    isKazakhText,
    cleanTitles,
    formatTitleCase
} from '../bot/services/teacherDirectoryService.js';

test('normalizeCyrillic correctly normalizes Kazakh and Russian letters and homoglyphs', () => {
    assert.equal(normalizeCyrillic('Қарағанды'), 'караганды');
    assert.equal(normalizeCyrillic('Әуезов'), 'ауезов');
    assert.equal(normalizeCyrillic('Ғабит'), 'габит');
    assert.equal(normalizeCyrillic('Үміт'), 'умит');
    assert.equal(normalizeCyrillic('Ұлан'), 'улан');
    assert.equal(normalizeCyrillic('Ілияс'), 'илияс');
    assert.equal(normalizeCyrillic('Өмір'), 'омир');
    assert.equal(normalizeCyrillic('Һәм'), 'хам');
    assert.equal(normalizeCyrillic('Ёлка'), 'елка');

    // Latin homoglyph replacement (e.g. typo on English keyboard)
    assert.equal(normalizeCyrillic('a c e o p x k'), 'а с е о р х к');

    // Punctuation and whitespace
    assert.equal(normalizeCyrillic('Попова, Н. В.'), 'попова н в');
});

test('isKazakhText detects specific Kazakh letters', () => {
    assert.equal(isKazakhText('Қазақстан'), true);
    assert.equal(isKazakhText('Әдебиет'), true);
    assert.equal(isKazakhText('Ғылым'), true);
    assert.equal(isKazakhText('Россия'), false);
    assert.equal(isKazakhText('Hello'), false);
    assert.equal(isKazakhText(''), false);
});

test('cleanTitles strips academic ranks, degrees, and prefixes', () => {
    assert.equal(cleanTitles('ст.преп. Попова Н. В.'), 'Попова Н. В.');
    assert.equal(cleanTitles('ст. пр. Нигай Е. В.'), 'Нигай Е. В.');
    assert.equal(cleanTitles('доц. Капашева Г. А.'), 'Капашева Г. А.');
    assert.equal(cleanTitles('проф. Лазарева Е. А.'), 'Лазарева Е. А.');
    assert.equal(cleanTitles('ассоц.проф. Жунусова М. Қ.'), 'Жунусова М. Қ.');
    assert.equal(cleanTitles('оқытушы Балжан А.'), 'Балжан А.');
    assert.equal(cleanTitles('аға оқытушы Сейтжанов Н.'), 'Сейтжанов Н.');
    assert.equal(cleanTitles('PhD докторы Төлегенов Б.'), 'Төлегенов Б.');
    assert.equal(cleanTitles('м.т.ғ.к. Оралбек М.'), 'Оралбек М.');
    // Ensure surnames like Доценко are not stripped
    assert.equal(cleanTitles('Доценко И. П.'), 'Доценко И. П.');
    assert.equal(cleanTitles('Прохоров А. С.'), 'Прохоров А. С.');
});

test('formatTitleCase standardizes uppercase/mixed case to neat Title Case', () => {
    assert.equal(formatTitleCase('ПОПОВА НАДЕЖДА ВИКТОРОВНА'), 'Попова Надежда Викторовна');
    assert.equal(formatTitleCase('жунусова меруерт қасымхановна'), 'Жунусова Меруерт Қасымхановна');
    assert.equal(formatTitleCase('КАСЫЛКАСОВА КАМИЛА НУРАЛИЕВНА'), 'Касылкасова Камила Нуралиевна');
});

test('teacherDirectoryService matches and resolves teacher names', () => {
    // Check clean formatting for student schedule
    const formatted = teacherDirectoryService.formatTeacherForStudent('ст.преп. Попова Н. В.');
    assert.ok(formatted);
    assert.ok(!formatted.startsWith('ст.преп.'));

    // Check findMatch or fallback
    const match = teacherDirectoryService.findMatch('ст.преп. Попова Н. В.');
    if (match) {
        assert.ok(match.fullName);
        assert.ok(match.lastName);
    }
});
