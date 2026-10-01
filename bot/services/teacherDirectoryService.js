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

class TeacherDirectoryService {
    constructor() {
        this.teachers = [];
        this.byInitialsMap = new Map();
        this.bySurnameMap = new Map();
        this.byFullNameMap = new Map();
        this._loadDirectory();
    }

    _loadDirectory() {
        try {
            const dataPath = path.resolve(__dirname, '../../data/teachers_full_directory.json');
            if (fs.existsSync(dataPath)) {
                const raw = fs.readFileSync(dataPath, 'utf8');
                const list = JSON.parse(raw);
                if (Array.isArray(list)) {
                    this.teachers = list;
                    this._buildIndexes();
                    log.info(`[TeacherDirectory] Успешно загружен справочник КарУ: ${this.teachers.length} преподавателей`);
                    return;
                }
            }
            log.warn(`[TeacherDirectory] Файл teachers_full_directory.json не найден по пути: ${dataPath}`);
        } catch (e) {
            log.error(`[TeacherDirectory] Ошибка загрузки справочника преподавателей: ${e.message}`);
        }
    }

    _buildIndexes() {
        this.byInitialsMap.clear();
        this.bySurnameMap.clear();
        this.byFullNameMap.clear();

        for (const t of this.teachers) {
            if (!t) continue;

            // 1. Индекс по инициалам (например: "попова н в")
            const normInitials = normalizeCyrillic(t.initials);
            if (normInitials) {
                if (!this.byInitialsMap.has(normInitials)) {
                    this.byInitialsMap.set(normInitials, []);
                }
                this.byInitialsMap.get(normInitials).push(t);
            }

            // 2. Индекс по фамилии (например: "попова")
            const normLastName = normalizeCyrillic(t.lastName);
            if (normLastName) {
                if (!this.bySurnameMap.has(normLastName)) {
                    this.bySurnameMap.set(normLastName, []);
                }
                this.bySurnameMap.get(normLastName).push(t);
            }

            // 3. Индекс по полному ФИО (например: "попова надежда викторовна")
            const normFullName = normalizeCyrillic(t.fullName);
            if (normFullName) {
                this.byFullNameMap.set(normFullName, t);
            }
        }
    }

    /**
     * Обогащение объекта преподавателя (из базы расписания) полными данными с сайта КарУ
     * @param {Object} teacher Объект из расписания ({ id, name, department, ... })
     * @returns {Object} Обогащенный объект преподавателя
     */
    enrich(teacher) {
        if (!teacher || !teacher.name) return teacher;

        const nameKey = normalizeCyrillic(teacher.name);

        // 1. Поиск по точному совпадению инициалов ("попова н в")
        let matches = this.byInitialsMap.get(nameKey);

        // Если не найдено, пробуем вариацию без пробелов между инициалами
        if (!matches || matches.length === 0) {
            const compactKey = nameKey.replace(/\s+/g, '');
            for (const [key, list] of this.byInitialsMap.entries()) {
                if (key.replace(/\s+/g, '') === compactKey) {
                    matches = list;
                    break;
                }
            }
        }

        // Если инициалы не подошли, пробуем совпадение по полному ФИО
        if (!matches || matches.length === 0) {
            const exactFull = this.byFullNameMap.get(nameKey);
            if (exactFull) {
                matches = [exactFull];
            }
        }

        if (matches && matches.length > 0) {
            // Если нашлось несколько человек с одинаковыми инициалами (коллизия),
            // пробуем сопоставить по названию или ID кафедры
            let best = matches[0];
            if (matches.length > 1 && teacher.department) {
                const targetDept = Number(teacher.department);
                const matchedByDept = matches.find(m => Number(m.departmentId) === targetDept);
                if (matchedByDept) best = matchedByDept;
            }

            return {
                ...teacher,
                fullName: best.fullName,
                firstName: best.firstName,
                lastName: best.lastName,
                patronymic: best.patronymic,
                jobTitle: best.jobTitle,
                photoUrl: best.photoUrl,
                isHead: !!best.isHead,
                departmentName: best.departmentName,
                departmentIdOnSite: best.departmentId
            };
        }

        // Если в справочнике нет (например, новый совместитель) — возвращаем как есть
        return teacher;
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

            // 2. Фамилия начинается с запроса
            if (normLast.startsWith(normQ)) {
                results.push({ teacher: t, score: 90 });
                continue;
            }

            // 3. Имя начинается с запроса (например: "Надежда", "Салтанат")
            if (normFirst.startsWith(normQ)) {
                results.push({ teacher: t, score: 80 });
                continue;
            }

            // 4. Многословный запрос (например: "Попова Надежда", "Танин Алибек", "Надежда Викторовна")
            // Каждый токен запроса должен быть префиксом хотя бы одного слова в ФИО
            const allTokensMatchPrefix = tokens.every(qTok => words.some(w => w.startsWith(qTok)));
            if (allTokensMatchPrefix) {
                results.push({ teacher: t, score: 70 });
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
