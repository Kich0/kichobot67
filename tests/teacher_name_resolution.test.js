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
    assert.equal(normalizeCyrillic('i y s h j'), 'и у с х й');

    // Spaced Kazakh patronymics
    assert.equal(normalizeCyrillic('Сағынтай ұлы'), 'сагынтайулы');
    assert.equal(normalizeCyrillic('Серік қызы'), 'сериккызы');
    assert.equal(normalizeCyrillic('Ахмет тегі'), 'ахметтеги');

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

test('SmartSearch: TeacherService.findByName resolves full names, word permutations and 1-letter typos', async () => {
    const { default: teacherService } = await import('../bot/services/teacherService.js');
    teacherService._cache = [
        {
            id: 1,
            name: 'Аяған Е. С.',
            fullName: 'Аяған Еркін Сағынтайұлы',
            firstName: 'Еркін',
            lastName: 'Аяған',
            patronymic: 'Сағынтайұлы',
            aliases: []
        },
        {
            id: 2,
            name: 'Попова Н. В.',
            fullName: 'Попова Надежда Викторовна',
            firstName: 'Надежда',
            lastName: 'Попова',
            patronymic: 'Викторовна',
            aliases: []
        },
        {
            id: 3,
            name: 'Жунусова Б. А.',
            fullName: 'Жунусова Баян Аманжоловна',
            firstName: 'Баян',
            lastName: 'Жунусова',
            patronymic: 'Аманжоловна',
            aliases: []
        },
        {
            id: 4,
            name: 'Нурмаханов Е. А.',
            fullName: 'Нурмаханов Еркин Аскарович',
            firstName: 'Еркин',
            lastName: 'Нурмаханов',
            patronymic: 'Аскарович',
            aliases: []
        }
    ];
    teacherService._lastCacheTime = Date.now();

    // 1. Поиск по точному полному ФИО (Аяған Еркін Сағынтайұлы)
    const resAyaganFull = await teacherService.findByName('Аяған Еркін Сағынтайұлы');
    assert.equal(resAyaganFull.length, 1);
    assert.equal(resAyaganFull[0].id, 1);

    // 2. Поиск с пробелом в казахском отчестве (Сағынтай ұлы)
    const resAyaganSpaced = await teacherService.findByName('Аяған Еркін Сағынтай ұлы');
    assert.equal(resAyaganSpaced.length, 1);
    assert.equal(resAyaganSpaced[0].id, 1);

    // 3. Поиск на русском языке (Аяган Еркин Сагынтай улы)
    const resAyaganRu = await teacherService.findByName('Аяган Еркин Сагынтай улы');
    assert.equal(resAyaganRu.length, 1);
    assert.equal(resAyaganRu[0].id, 1);

    // 4. Поиск с перестановкой слов (Еркін Сағынтайұлы Аяған)
    const resAyaganPerm = await teacherService.findByName('Еркін Сағынтайұлы Аяған');
    assert.equal(resAyaganPerm.length, 1);
    assert.equal(resAyaganPerm[0].id, 1);

    // 5. Поиск по имени: "Еркін" находит обоих преподавателей
    const resErkin = await teacherService.findByName('Еркін');
    assert.equal(resErkin.length, 2);
    assert.ok(resErkin.some(t => t.id === 1));
    assert.ok(resErkin.some(t => t.id === 4));

    // 6. Опечатка в 1 букву в фамилии: "Попово" -> находит Попову Н. В.
    const resPopovo = await teacherService.findByName('Попово');
    assert.equal(resPopovo.length, 1);
    assert.equal(resPopovo[0].id, 2);

    // 7. Опечатка в 1 букву в фамилии: "Аягон" -> находит Аяған
    const resAyagon = await teacherService.findByName('Аягон');
    assert.equal(resAyagon.length, 1);
    assert.equal(resAyagon[0].id, 1);

    // 8. Опечатка в 1 букву в длинном ФИО: "Попова Надежда Виктаровна"
    const resPopovaTypo = await teacherService.findByName('Попова Надежда Виктаровна');
    assert.equal(resPopovaTypo.length, 1);
    assert.equal(resPopovaTypo[0].id, 2);

    // 9. Опечатка в 1 букву в длинном ФИО: "Аяган Еркин Сагинтайулы"
    const resAyaganTypo = await teacherService.findByName('Аяган Еркин Сагинтайулы');
    assert.equal(resAyaganTypo.length, 1);
    assert.equal(resAyaganTypo[0].id, 1);

    // 10. Опечатка в 1 букву в многословном запросе: "Жунусава Баян"
    const resZhunusava = await teacherService.findByName('Жунусава Баян');
    assert.equal(resZhunusava.length, 1);
    assert.equal(resZhunusava[0].id, 3);
});

test('SmartSearch: GroupService.findByName handles Latin letters and 1-letter typos', async () => {
    const { default: groupService } = await import('../bot/services/groupService.js');
    groupService._cache = [
        { id: 101, name: 'ИС-21-1' },
        { id: 102, name: 'ВТиПО-22' },
        { id: 103, name: 'ЮР-23' }
    ];
    groupService._lastCacheTime = Date.now();

    // 1. Поиск латиницей "IS-21-1" -> находит "ИС-21-1"
    const resLatin = await groupService.findByName('IS-21-1');
    assert.equal(resLatin.length, 1);
    assert.equal(resLatin[0].name, 'ИС-21-1');

    // 2. Поиск с опечаткой в 1 букву "ВТПО" -> находит "ВТиПО-22"
    const resTypo = await groupService.findByName('ВТПО');
    assert.equal(resTypo.length, 1);
    assert.equal(resTypo[0].name, 'ВТиПО-22');
});

