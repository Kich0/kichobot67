import log from "../logging/logging.js";
import HtmlService from "./HtmlService.js";
import config from "../config.js";
import KsuAuthService from "./KsuAuthService.js";
import FreeProxyService from "./FreeProxyService.js";
import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import * as cheerio from "cheerio";


export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

const SCHEDULE_TIMEOUT = 15000; // 15 сек на скачивание расписания

class ScheduleService {

    /**
     * Скачать HTML страницы через axios (БЕЗ Puppeteer).
     * Стратегия: сначала напрямую, потом через прокси.
     * @param {string} url - URL страницы
     * @param {string} cookie - Кука авторизации
     * @param {boolean} mustIncludeTable - Требовать ли наличие <table в ответе (для расписания)
     */
    async _fetchHtml(url, cookie, mustIncludeTable = false, attempt = 1, maxAttempts = 5) {
        // Попытка 1: напрямую (без прокси)
        if (attempt === 1) {
            try {
                const res = await axios.get(url, {
                    timeout: SCHEDULE_TIMEOUT,
                    maxRedirects: 0,
                    validateStatus: () => true,
                    headers: {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Cookie': cookie
                    }
                });

                if (res.status === 302 || res.status === 301) {
                    log.warn("[Schedule] Редирект — сессия устарела, обновляю куку...");
                    KsuAuthService.invalidate();
                    const newCookie = await KsuAuthService.getCookie();
                    return this._fetchHtml(url, newCookie, mustIncludeTable, attempt + 1, maxAttempts);
                }

                if (res.status === 200 && typeof res.data === 'string') {
                    if (!mustIncludeTable || res.data.includes('<table')) {
                        return res.data;
                    }
                    log.warn("[Schedule] Прямой запрос вернул страницу без таблицы, пробую через прокси...");
                }
            } catch (e) {
                log.warn(`[Schedule] Прямой запрос не удался: ${e.message}`);
            }
        }

        // Попытки через прокси
        for (let i = attempt; i <= maxAttempts; i++) {
            const proxy = FreeProxyService.getNextProxy();
            if (!proxy) {
                log.warn(`[Schedule] Попытка ${i}/${maxAttempts}: нет прокси, жду...`);
                await sleep(2000);
                continue;
            }

            try {
                const httpsAgent = new HttpsProxyAgent(`http://${proxy}`, { rejectUnauthorized: false });
                const httpAgent = new HttpProxyAgent(`http://${proxy}`);

                const res = await axios.get(url, {
                    httpsAgent,
                    httpAgent,
                    proxy: false,
                    timeout: SCHEDULE_TIMEOUT,
                    maxRedirects: 0,
                    validateStatus: () => true,
                    headers: {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Cookie': cookie
                    }
                });

                if (res.status === 302 || res.status === 301) {
                    log.warn(`[Schedule] Прокси ${proxy}: Редирект — сессия устарела`);
                    KsuAuthService.invalidate();
                    cookie = await KsuAuthService.getCookie();
                    continue; // пробуем с новой кукой
                }

                if ([400, 403, 502, 503, 504].includes(res.status)) {
                    log.warn(`[Schedule] Прокси ${proxy}: статус ${res.status} — помечаю мёртвым`);
                    FreeProxyService.markProxyDead(proxy);
                    continue;
                }

                if (res.status === 200 && typeof res.data === 'string') {
                    if (res.data.includes('Cloudflare') || res.data.includes('Just a moment')) {
                        log.warn(`[Schedule] Прокси ${proxy}: Cloudflare — помечаю мёртвым`);
                        FreeProxyService.markProxyDead(proxy);
                        continue;
                    }
                    if (res.data.includes('Forbidden')) {
                        log.warn(`[Schedule] Прокси ${proxy}: Forbidden — помечаю мёртвым`);
                        FreeProxyService.markProxyDead(proxy);
                        continue;
                    }
                    if (!mustIncludeTable || res.data.includes('<table')) {
                        return res.data;
                    }
                    // Страница без таблицы — возможно нужна переавторизация
                    log.warn(`[Schedule] Прокси ${proxy}: страница без таблицы (статус ${res.status})`);
                    KsuAuthService.invalidate();
                    cookie = await KsuAuthService.getCookie();
                    continue;
                }
            } catch (e) {
                const msg = e.message || '';
                if (msg.includes('ECONNRESET') || msg.includes('ETIMEDOUT') || msg.includes('ECONNREFUSED') || msg.includes('ERR_TUNNEL')) {
                    FreeProxyService.markProxyDead(proxy);
                }
                log.warn(`[Schedule] Прокси ${proxy}: ошибка — ${msg}`);
            }
        }

        throw new Error(`Не удалось скачать ${url} после ${maxAttempts} попыток`);
    }

    async _fetchScheduleHtml(url, cookie) {
        return this._fetchHtml(url, cookie, true);
    }

    /**
     * Получить расписание студента по groupId через axios+cheerio.
     * ОСНОВНОЙ МЕТОД — вызывается из бота.
     */
    get_schedule_by_groupId = async (id, language) => {
        const cookie = await KsuAuthService.getCookie();
        const url = encodeURI(`${config.KSU_DOMAIN}/view1.php?id=${id}&Otdel=${language}`);

        const html = await this._fetchScheduleHtml(url, cookie);

        // Парсим HTML через cheerio
        const tableHTML = this._extractTableHtml(html);
        if (!tableHTML) {
            throw new Error("Таблица расписания не найдена в HTML");
        }

        return this._parseScheduleTable(tableHTML, language);
    }

    /**
     * Извлечь HTML первой таблицы из страницы
     */
    _extractTableHtml(html) {
        const $ = cheerio.load(html);
        const table = $('table').first();
        return table.length > 0 ? $.html(table) : null;
    }

    /**
     * Распарсить HTML таблицы расписания в структурированные данные
     */
    _parseScheduleTable(tableHTML, language) {
        function removeBrTags(text) {
            if (text.includes('<br>')) {
                return removeBrTags(text.replace('<br>', '\n'));
            } else {
                return text;
            }
        }

        const tableData = HtmlService.htmlTableToJson(tableHTML);

        const headers = tableData.shift();
        const schedule_data = tableData.map(row => {
            const obj = {};
            headers.forEach((header, index) => {
                obj[header] = row[index];
            });
            return obj;
        });

        let days_list = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
        if (language === "каз") {
            days_list = ['Дүйсенбі', 'Сейсенбі', 'Сәрсенбі', 'Бейсенбі', 'Жұма', 'Сенбі'];
        }

        let schedule = [];
        let item_number = 0;
        for (let i = 0; i < 6; i++) {
            let daily_subjects = [];
            let day = '';
            for (let j = 0; j < 13; j++) {
                let item = schedule_data[item_number];
                if (j === 0) {
                    day = days_list[i];
                } else {
                    const values = Object.values(item);
                    item = {
                        [Object.keys(item)[0]]: day,
                        [Object.keys(item)[1]]: values[0],
                        [Object.keys(item)[2]]: values[1],
                    };
                }
                const values = Object.values(item);
                if (values[2] === "&nbsp;") {
                    values[2] = "";
                }
                daily_subjects.push({
                    time: values[1],
                    subject: removeBrTags(values[2])
                });

                item_number += 1;
            }

            const firstSubjectIndex = daily_subjects.findIndex(item => item.subject !== '');
            let trimmedDailySubjects = [];
            if (firstSubjectIndex !== -1) {
                const lastSubjectIndex = daily_subjects.reverse().findIndex(item => item.subject !== '');
                daily_subjects.reverse();
                trimmedDailySubjects = daily_subjects.slice(firstSubjectIndex, daily_subjects.length - lastSubjectIndex);
            } else {
                trimmedDailySubjects = [];
            }

            let daily_schedule = {
                day,
                subjects: trimmedDailySubjects
            };

            schedule.push(daily_schedule);
        }

        // Проверка на кривое расписание
        for (const daily_schedule of schedule) {
            for (const subject of daily_schedule.subjects) {
                if (subject.subject === "\n") {
                    log.warn("[Schedule] Кривое расписание обнаружено, но без Puppeteer — возвращаем как есть. Group: " + arguments[0]);
                }
            }
        }

        return schedule;
    }

    // ===========================
    // МЕТОДЫ СИНХРОНИЗАЦИИ (Axios + Cheerio БЕЗ Puppeteer)
    // ===========================

    /**
     * Получить список всех факультетов из КарГУ
     */
    get_faculty_list = async () => {
        const cookie = await KsuAuthService.getCookie();
        const url = `${config.KSU_DOMAIN}/`;
        const html = await this._fetchHtml(url, cookie, false);
        const $ = cheerio.load(html);
        const faculties = [];
        $('select[name="Login"] option').each((i, el) => {
            const text = $(el).text().trim();
            if (text) {
                faculties.push({ name: text, id: i });
            }
        });
        if (faculties.length === 0) {
            throw new Error("Список факультетов не найден на главной странице КарГУ");
        }
        return faculties;
    }

    /**
     * Получить программы по названию факультета
     */
    get_programs_by_faculty_name = async (facultyName, facultyId) => {
        let cookie = await KsuAuthService.getCookie();
        // POST выбор факультета для сохранения в PHP-сессии
        const postRes = await axios.post(`${config.KSU_DOMAIN}/index.php?x`,
            `Login=${encodeURIComponent(facultyName)}&pw=`,
            {
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Cookie': cookie,
                    'Origin': config.KSU_DOMAIN,
                    'Referer': `${config.KSU_DOMAIN}/`
                },
                timeout: SCHEDULE_TIMEOUT,
                maxRedirects: 0,
                validateStatus: () => true
            }
        );

        if (postRes.headers && postRes.headers['set-cookie']) {
            const match = postRes.headers['set-cookie'][0].match(/PHPSESSID=([^;]+)/);
            if (match) cookie = `PHPSESSID=${match[1]}`;
        }

        // GET /stud.php со списком программ
        const html = await this._fetchHtml(`${config.KSU_DOMAIN}/stud.php`, cookie, false);
        const $ = cheerio.load(html);
        const programs = [];
        $('a').each((_, el) => {
            const href = $(el).attr('href') || '';
            if (href.includes('grupps')) {
                const idMatch = href.match(/id=(\d+)/);
                if (idMatch) {
                    programs.push({
                        name: $(el).text().trim(),
                        href,
                        id: Number(idMatch[1]),
                        facultyId,
                        facultyName
                    });
                }
            }
        });
        return programs;
    }

    /**
     * Получить список программ по facultyId (совместимость с сигнатурами)
     */
    get_program_list_by_facultyId = async (...args) => {
        // Поддержка старых вызовов: (browser, faculties_data, id) или (faculties_data, id) или (facultyId, facultyName)
        let facultyId = args[0];
        let faculties_data = args[1];
        if (args.length === 3) {
            faculties_data = args[1];
            facultyId = args[2];
        }

        let facultyName = null;
        if (Array.isArray(faculties_data)) {
            const found = faculties_data.find(f => f.id === facultyId) || faculties_data[facultyId];
            if (found) facultyName = found.name;
        } else if (typeof faculties_data === 'string') {
            facultyName = faculties_data;
        }

        if (!facultyName) {
            const faculties = await this.get_faculty_list();
            const found = faculties.find(f => f.id === facultyId) || faculties[facultyId];
            facultyName = found?.name;
        }

        if (!facultyName) {
            throw new Error(`Не удалось определить название факультета для id ${facultyId}`);
        }

        return await this.get_programs_by_faculty_name(facultyName, facultyId);
    }

    /**
     * Получить список групп одной программы по programId
     */
    get_group_list_by_programId = async (...args) => {
        // Поддержка вызовов: (id) или (browser, id)
        const id = args.length > 1 ? args[1] : args[0];
        const cookie = await KsuAuthService.getCookie();
        const url = `${config.KSU_DOMAIN}/grupps1.php?id=${id}`;
        const html = await this._fetchHtml(url, cookie, false);
        const $ = cheerio.load(html);
        const groups = [];

        $('table tr').each((_, tr) => {
            const firstTd = $(tr).find('td').first();
            const link = firstTd.find('a');
            if (link.length > 0) {
                const href = link.attr('href') || '';
                const idMatch = href.match(/id=(\d+)/);
                const langMatch = href.match(/Otdel=([^&]+)/);
                const kursMatch = href.match(/Kurs=(\d+)/);
                const studMatch = href.match(/Stud=(\d+)/);
                const name = link.text().trim();
                if (idMatch && name) {
                    groups.push({
                        name,
                        id: Number(idMatch[1]),
                        href,
                        language: langMatch ? langMatch[1] : '',
                        age: kursMatch ? Number(kursMatch[1]) : 1,
                        studentCount: studMatch ? Number(studMatch[1]) : 0,
                        programId: Number(id),
                        program: Number(id)
                    });
                }
            }
        });
        return groups;
    }

    /**
     * Быстрый параллельный сбор всех групп по списку программ (10-15 сек на все 240 программ)
     */
    get_all_groups_fast = async (programs, onProgress) => {
        const cookie = await KsuAuthService.getCookie();
        const allGroups = [];
        const BATCH_SIZE = 4;

        for (let i = 0; i < programs.length; i += BATCH_SIZE) {
            const batch = programs.slice(i, i + BATCH_SIZE);
            const results = await Promise.all(batch.map(async (prog) => {
                let attempts = 0;
                while (attempts < 3) {
                    attempts++;
                    try {
                        const res = await axios.get(`${config.KSU_DOMAIN}/grupps1.php?id=${prog.id}`, {
                            headers: { 
                                'Cookie': cookie, 
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' 
                            },
                            timeout: 10000,
                            validateStatus: () => true
                        });
                        if (res.status === 200 && typeof res.data === 'string') {
                            const $ = cheerio.load(res.data);
                            const progGroups = [];
                            $('table tr').each((_, tr) => {
                                const firstTd = $(tr).find('td').first();
                                const link = firstTd.find('a');
                                if (link.length > 0) {
                                    const href = link.attr('href') || '';
                                    const idMatch = href.match(/id=(\d+)/);
                                    const langMatch = href.match(/Otdel=([^&]+)/);
                                    const kursMatch = href.match(/Kurs=(\d+)/);
                                    const studMatch = href.match(/Stud=(\d+)/);
                                    const name = link.text().trim();
                                    if (idMatch && name) {
                                        progGroups.push({
                                            name,
                                            id: Number(idMatch[1]),
                                            href,
                                            language: langMatch ? langMatch[1] : '',
                                            age: kursMatch ? Number(kursMatch[1]) : 1,
                                            studentCount: studMatch ? Number(studMatch[1]) : 0,
                                            program: prog.id,
                                            programId: prog.id
                                        });
                                    }
                                }
                            });
                            return progGroups;
                        } else if (res.status === 403 || res.status === 429) {
                            await sleep(1000);
                        }
                    } catch (e) {
                        if (attempts >= 3) {
                            log.warn(`[FastGroups] Ошибка программы ${prog.id} (${prog.name}): ${e.message}`);
                        }
                        await sleep(500);
                    }
                }
                return [];
            }));

            for (const progGroups of results) {
                allGroups.push(...progGroups);
            }

            if (onProgress) {
                const stage = Math.floor(Math.min(i + BATCH_SIZE, programs.length) / programs.length * 100);
                onProgress(stage, allGroups.length);
            }

            await sleep(250);
        }

        return allGroups;
    }
}

export default new ScheduleService();