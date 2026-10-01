import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import log from '../logging/logging.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Нормализация казахских и русских букв для симметричного и нечувствительного к раскладке поиска.
 * Қ ⇄ К, Ә ⇄ А, Ұ/Ү ⇄ У, І ⇄ И, Ғ ⇄ Г, Ө ⇄ О, Һ ⇄ Х, Ё ⇄ Е.
 * Также очищает знаки препинания и заменяет латинские буквы-двойники (c, a, e, o, p, x, k) на кириллицу.
 */
export function normalizeCyrillic(str) {
    if (!str) return '';
    return String(str).toLowerCase()
        // Казахские специфические буквы -> общие фонетические кириллические эквиваленты
        .replace(/ә/g, 'а')
        .replace(/і/g, 'и')
        .replace(/ң/g, 'н')
        .replace(/ғ/g, 'г')
        .replace(/ү/g, 'у')
        .replace(/ұ/g, 'у')
        .replace(/қ/g, 'к')
        .replace(/ө/g, 'о')
        .replace(/һ/g, 'х')
        .replace(/ё/g, 'е')
        // Латинские гомоглифы на случай опечаток клавиатуры
        .replace(/a/g, 'а')
        .replace(/c/g, 'с')
        .replace(/e/g, 'е')
        .replace(/o/g, 'о')
        .replace(/p/g, 'р')
        .replace(/x/g, 'х')
        .replace(/k/g, 'к')
        // Знаки препинания и дефисы -> пробелы
        .replace(/[-_.,/\\()[\]{}|:;!?"'`]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

/**
 * Проверка: содержит ли текст специфические казахские буквы
 */
export function isKazakhText(str) {
    if (!str) return false;
    return /[әіңғүұқөһӘІҢҒҮҰҚӨҺ]/i.test(str);
}

/**
 * Очистка академических званий и префиксов (например: "ст.преп. Попова Н. В." -> "Попова Н. В.")
 */
export function cleanTitles(str) {
    if (!str) return '';
    let s = String(str).trim();
    let prev = '';
    while (s !== prev) {
        prev = s;
        s = s.replace(/^(?:ст\.?\s*преп(?:одаватель)?\.?|преп(?:одаватель)?\.?|ст\.?\s*пр\.?|пр\.?|доц(?:ент)?\.?|проф(?:ессор)?\.?|асс(?:истент)?\.?|ассоц\.?\s*проф(?:ессор)?\.?|ассис\.?\s*проф(?:ессор)?\.?|аcсоц\.?\s*проф(?:ессор)?\.?|аcсис\.?\s*проф(?:ессор)?\.?|аға\s+оқытушы|оқытушы|м\.т\.ғ\.к\.?|п\.ғ\.к\.?|к\.[а-яё\-]+\.?[нм]?\.?|phd(?:\s*докторы)?)\s+/iu, '').trim();
    }
    return s;
}

/**
 * Приведение ФИО к красивому регистру (Title Case)
 * "КАСЫЛКАСОВА КАМИЛА НУРАЛИЕВНА" -> "Касылкасова Камила Нуралиевна"
 */
export function formatTitleCase(str) {
    if (!str) return '';
    return str
        .toLowerCase()
        .replace(/(?:^|\s|-)[а-яёa-zәіңғүұқөһ]/gu, c => c.toUpperCase());
}

class TeacherDirectoryService {
    constructor() {
        this.teachers = [];
        this.byInitialsMap = new Map();
        this.bySingleInitialMap = new Map();
        this.bySurnameMap = new Map();
        this.byFullNameMap = new Map();
        this._loadDirectory();
    }

    _loadDirectory() {
        try {
            const dataPath = path.resolve(__dirname, '../../data/teachers_full_directory.json');
            const customPath = path.resolve(__dirname, '../../data/teachers_custom_directory.json');
            const combined = [];

            if (fs.existsSync(dataPath)) {
                const raw = fs.readFileSync(dataPath, 'utf8');
                const list = JSON.parse(raw);
                if (Array.isArray(list)) {
                    combined.push(...list);
                }
            } else {
                log.warn(`[TeacherDirectory] Файл teachers_full_directory.json не найден по пути: ${dataPath}`);
            }

            if (fs.existsSync(customPath)) {
                const rawCustom = fs.readFileSync(customPath, 'utf8');
                const listCustom = JSON.parse(rawCustom);
                if (Array.isArray(listCustom)) {
                    combined.push(...listCustom);
                }
            }

            if (combined.length > 0) {
                this.teachers = combined.map(t => ({
                    ...t,
                    fullName: formatTitleCase(t.fullName),
                    firstName: formatTitleCase(t.firstName),
                    lastName: formatTitleCase(t.lastName),
                    patronymic: formatTitleCase(t.patronymic)
                }));
                this._buildIndexes();
                log.info(`[TeacherDirectory] Успешно загружен справочник КарУ: ${this.teachers.length} преподавателей`);
                return;
            }
        } catch (e) {
            log.error(`[TeacherDirectory] Ошибка загрузки справочника преподавателей: ${e.message}`);
        }
    }

    _buildIndexes() {
        this.byInitialsMap.clear();
        this.bySingleInitialMap.clear();
        this.bySurnameMap.clear();
        this.byFullNameMap.clear();

        for (const t of this.teachers) {
            if (!t) continue;

            // 1. Полное ФИО
            const normFullName = normalizeCyrillic(t.fullName);
            if (normFullName) {
                this.byFullNameMap.set(normFullName, t);
            }

            // 2. Фамилия и инициалы
            const normLastName = normalizeCyrillic(t.lastName);
            if (normLastName) {
                if (!this.bySurnameMap.has(normLastName)) {
                    this.bySurnameMap.set(normLastName, []);
                }
                this.bySurnameMap.get(normLastName).push(t);

                const firstInit = normalizeCyrillic(t.firstName)?.[0];
                if (firstInit) {
                    // Индекс по 1 инициалу: "фамилия и"
                    const singleKey = normLastName + ' ' + firstInit;
                    if (!this.bySingleInitialMap.has(singleKey)) {
                        this.bySingleInitialMap.set(singleKey, []);
                    }
                    this.bySingleInitialMap.get(singleKey).push(t);

                    const patrInit = normalizeCyrillic(t.patronymic)?.[0];
                    if (patrInit) {
                        // Индекс по 2 инициалам: "фамилия и о"
                        const doubleKey = normLastName + ' ' + firstInit + ' ' + patrInit;
                        if (!this.byInitialsMap.has(doubleKey)) {
                            this.byInitialsMap.set(doubleKey, []);
                        }
                        this.byInitialsMap.get(doubleKey).push(t);
                    }
                }
            }

            // Дополнительный индекс по строке initials из справочника
            if (t.initials) {
                const normInitials = normalizeCyrillic(cleanTitles(t.initials));
                if (normInitials) {
                    if (!this.byInitialsMap.has(normInitials)) {
                        this.byInitialsMap.set(normInitials, []);
                    }
                    if (!this.byInitialsMap.get(normInitials).includes(t)) {
                        this.byInitialsMap.get(normInitials).push(t);
                    }
                }
            }

            // Индексируем псевдонимы (aliases), если они есть
            if (t.aliases && Array.isArray(t.aliases)) {
                for (const alias of t.aliases) {
                    const cleanA = cleanTitles(alias);
                    const normA = normalizeCyrillic(cleanA);
                    if (!normA) continue;

                    // Добавляем в byFullNameMap если нет
                    if (!this.byFullNameMap.has(normA)) {
                        this.byFullNameMap.set(normA, t);
                    }

                    // Анализируем структуру псевдонима
                    const aWords = normA.split(' ').filter(Boolean);
                    if (aWords.length === 1) {
                        // Одиночная фамилия-синоним (например "айденова", "адикенова", "казыгулов", "толегенов")
                        const aSurname = aWords[0];
                        if (!this.bySurnameMap.has(aSurname)) {
                            this.bySurnameMap.set(aSurname, []);
                        }
                        if (!this.bySurnameMap.get(aSurname).includes(t)) {
                            this.bySurnameMap.get(aSurname).push(t);
                        }

                        // Привязываем инициалы t к этой фамилии-синониму
                        const fInit = normalizeCyrillic(t.firstName)?.[0];
                        if (fInit) {
                            const sKey = aSurname + ' ' + fInit;
                            if (!this.bySingleInitialMap.has(sKey)) {
                                this.bySingleInitialMap.set(sKey, []);
                            }
                            if (!this.bySingleInitialMap.get(sKey).includes(t)) {
                                this.bySingleInitialMap.get(sKey).push(t);
                            }
                            const pInit = normalizeCyrillic(t.patronymic)?.[0];
                            if (pInit) {
                                const dKey = aSurname + ' ' + fInit + ' ' + pInit;
                                if (!this.byInitialsMap.has(dKey)) {
                                    this.byInitialsMap.set(dKey, []);
                                }
                                if (!this.byInitialsMap.get(dKey).includes(t)) {
                                    this.byInitialsMap.get(dKey).push(t);
                                }
                            }
                        }
                    } else if (aWords.length === 2 && aWords[1].length === 1) {
                        // "фамилия и" (например "айденова б", "тулегенов б")
                        if (!this.bySingleInitialMap.has(normA)) {
                            this.bySingleInitialMap.set(normA, []);
                        }
                        if (!this.bySingleInitialMap.get(normA).includes(t)) {
                            this.bySingleInitialMap.get(normA).push(t);
                        }
                    } else if (aWords.length >= 2) {
                        // Инициалы или имя с отчеством
                        if (!this.byInitialsMap.has(normA)) {
                            this.byInitialsMap.set(normA, []);
                        }
                        if (!this.byInitialsMap.get(normA).includes(t)) {
                            this.byInitialsMap.get(normA).push(t);
                        }
                    }
                }
            }
        }
    }

    /**
     * Поиск преподавателя по любой строке (с академическими званиями, инициалами, без точек и пробелов)
     * @param {string} rawStr - Строка (например: "ст.преп. Попова Н. В.", "Гельмле А.М.", "Адильбаев А.")
     * @param {number|string} deptId - Опциональный ID кафедры для разрешения коллизий однофамильцев
     * @returns {Object|null} Объект преподавателя из справочника
     */
    findMatch(rawStr, deptId = null) {
        if (!rawStr) return null;
        const clean = cleanTitles(rawStr);
        const norm = normalizeCyrillic(clean);
        if (!norm) return null;

        // 1. По точному полному имени
        if (this.byFullNameMap.has(norm)) {
            return this.byFullNameMap.get(norm);
        }

        const words = norm.split(' ').filter(Boolean);
        if (words.length === 0) return null;

        let surname = '', inits = [];
        if (words.length >= 2 && words[0].length === 1) {
            // Формат: "И. О. Фамилия"
            surname = words[words.length - 1];
            inits = words.slice(0, words.length - 1).map(w => w[0]);
        } else {
            // Формат: "Фамилия И. О."
            surname = words[0];
            inits = words.slice(1).map(w => w[0]);
        }

        // 2. Ищем по двум инициалам: surname + inits[0] + inits[1]
        if (inits.length >= 2) {
            const key2 = surname + ' ' + inits[0] + ' ' + inits[1];
            const found2 = this.byInitialsMap.get(key2);
            if (found2 && found2.length > 0) {
                if (found2.length === 1) return found2[0];
                if (deptId) {
                    const matchDept = found2.find(t => Number(t.departmentId) === Number(deptId));
                    if (matchDept) return matchDept;
                }
                return found2[0];
            }
        }

        // 3. Ищем по одному инициалу: surname + inits[0]
        if (inits.length >= 1) {
            const key1 = surname + ' ' + inits[0];
            const found1 = this.bySingleInitialMap.get(key1);
            if (found1 && found1.length > 0) {
                if (found1.length === 1) return found1[0];
                if (deptId) {
                    const matchDept = found1.find(t => Number(t.departmentId) === Number(deptId));
                    if (matchDept) return matchDept;
                }
                return found1[0];
            }
        }

        // 4. Ищем только по фамилии
        const foundSurname = this.bySurnameMap.get(surname);
        if (foundSurname && foundSurname.length > 0) {
            if (foundSurname.length === 1) return foundSurname[0];
            if (deptId) {
                const matchDept = foundSurname.find(t => Number(t.departmentId) === Number(deptId));
                if (matchDept) return matchDept;
            }
            return foundSurname[0];
        }

        return null;
    }

    /**
     * Обогащение объекта преподавателя полными данными со справочника КарУ
     * @param {Object|string} teacher Объект из расписания ({ id, name, department, ... }) или строка с именем
     * @returns {Object} Обогащенный объект преподавателя
     */
    enrich(teacher) {
        if (!teacher) return teacher;
        const rawName = typeof teacher === 'string' ? teacher : teacher.name;
        if (!rawName) return teacher;

        const deptId = typeof teacher === 'object' ? (teacher.department || teacher.departmentId) : null;
        const matched = this.findMatch(rawName, deptId);

        if (matched) {
            const cleanFullName = formatTitleCase(matched.fullName);
            return {
                ...(typeof teacher === 'object' ? teacher : { name: rawName }),
                fullName: cleanFullName,
                firstName: formatTitleCase(matched.firstName),
                lastName: formatTitleCase(matched.lastName),
                patronymic: formatTitleCase(matched.patronymic),
                aliases: matched.aliases || [],
                jobTitle: matched.jobTitle,
                photoUrl: matched.photoUrl,
                isHead: !!matched.isHead,
                departmentName: matched.departmentName,
                departmentIdOnSite: matched.departmentId
            };
        }

        // Если в справочнике не найден — возвращаем очищенное от званий имя
        const cleanRaw = cleanTitles(rawName);
        if (typeof teacher === 'object') {
            return {
                ...teacher,
                fullName: teacher.fullName || cleanRaw
            };
        }
        return { name: cleanRaw, fullName: cleanRaw };
    }

    /**
     * Форматирование строки преподавателя для расписания студента:
     * Возвращает чистое полное ФИО: "Попова Надежда Викторовна"
     */
    formatTeacherForStudent(rawTeacherName) {
        if (!rawTeacherName) return '';
        const match = this.findMatch(rawTeacherName);
        if (match) {
            return formatTitleCase(match.fullName);
        }
        return cleanTitles(rawTeacherName);
    }

    /**
     * Префиксный поиск преподавателей по имени, отчеству или фамилии
     * с казахско-русской нормализацией.
     * @param {string} query Текст поискового запроса (например: "Надежда", "на", "Алибек", "ис")
     * @returns {Array} Список найденных преподавателей
     */
    searchByFullName(query) {
        const normQ = normalizeCyrillic(query);
        if (!normQ || normQ.length < 2) return [];

        const tokens = normQ.split(' ').filter(Boolean);
        if (tokens.length === 0) return [];

        const results = [];

        for (const t of this.teachers) {
            if (!t || !t.fullName) continue;

            const normFull = normalizeCyrillic(t.fullName);
            const normLast = normalizeCyrillic(t.lastName);
            const normFirst = normalizeCyrillic(t.firstName);
            const normPatr = normalizeCyrillic(t.patronymic);
            const words = [normLast, normFirst, normPatr].filter(Boolean);

            // КРИТИЧЕСКОЕ ПРАВИЛО: Для коротких запросов (<= 2 символов, например "на", "ис")
            // ищем СТРОГО по началу фамилии или имени! Никаких суффиксов в отчествах!
            if (tokens.length === 1 && normQ.length <= 2) {
                if (normLast.startsWith(normQ) || normFirst.startsWith(normQ)) {
                    results.push({ teacher: t, score: normLast.startsWith(normQ) ? 100 : 50 });
                }
                continue;
            }

            // Для запросов от 3 символов:
            // 1. Точное совпадение с фамилией
            if (normLast === normQ) {
                results.push({ teacher: t, score: 120 });
                continue;
            }

            // 1.1 Точное совпадение с именем
            if (normFirst === normQ) {
                results.push({ teacher: t, score: 115 });
                continue;
            }

            // 1.2 Совпадение по псевдонимам (aliases)
            if (t.aliases && Array.isArray(t.aliases)) {
                const aliasMatched = t.aliases.some(alias => {
                    const normAlias = normalizeCyrillic(alias);
                    return normAlias === normQ || normAlias.startsWith(normQ) || (tokens.length > 1 && normAlias.includes(normQ));
                });
                if (aliasMatched) {
                    results.push({ teacher: t, score: 110 });
                    continue;
                }
            }

            // 2. Фамилия начинается с запроса
            if (normLast.startsWith(normQ)) {
                results.push({ teacher: t, score: 95 });
                continue;
            }

            // 3. Имя начинается с запроса (например: "Надежда", "Салтанат")
            if (normFirst.startsWith(normQ)) {
                results.push({ teacher: t, score: 85 });
                continue;
            }

            // 3.1 Отчество начинается с запроса (например: "Камелович")
            if (normPatr && normPatr.startsWith(normQ)) {
                results.push({ teacher: t, score: 75 });
                continue;
            }

            // 4. Многословный запрос (например: "Попова Надежда", "Саликов Нурсултан", "Надежда Викторовна")
            // Каждый токен запроса должен быть префиксом хотя бы одного слова в ФИО
            const allTokensMatchPrefix = tokens.every(qTok => words.some(w => w.startsWith(qTok)));
            if (allTokensMatchPrefix) {
                results.push({ teacher: t, score: 105 });
                continue;
            }
        }

        // Сортировка: релевантность -> заведующие кафедрами выше -> алфавитный порядок
        results.sort((a, b) => {
            if (b.score !== a.score) return b.score - a.score;
            if (b.teacher.isHead !== a.teacher.isHead) return (b.teacher.isHead ? 1 : 0) - (a.teacher.isHead ? 1 : 0);
            return a.teacher.fullName.localeCompare(b.teacher.fullName, 'ru');
        });

        return results.map(r => r.teacher);
    }
}

export default new TeacherDirectoryService();
