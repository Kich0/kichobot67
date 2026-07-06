import log from "../logging/logging.js";
import HtmlService from "./HtmlService.js";
import config from "../config.js";
import KsuAuthService from "./KsuAuthService.js";
import FreeProxyService from "./FreeProxyService.js";
import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import * as cheerio from "cheerio";

// Puppeteer-зависимости ТОЛЬКО для get_faculty_list, get_program_list, get_group_list
// (они нужны для SyncService и начального парсинга через browser)
import BrowserController from "../controllers/BrowserController.js";

export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

const SCHEDULE_TIMEOUT = 15000; // 15 сек на скачивание расписания

class ScheduleService {

    /**
     * Скачать HTML расписания через axios (БЕЗ Puppeteer).
     * Стратегия: сначала напрямую, потом через прокси.
     */
    async _fetchScheduleHtml(url, cookie, attempt = 1, maxAttempts = 5) {
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
                    return this._fetchScheduleHtml(url, newCookie, attempt + 1, maxAttempts);
                }

                if (res.status === 200 && typeof res.data === 'string' && res.data.includes('<table')) {
                    return res.data;
                }

                // Страница не содержит таблицу — возможно нужна авторизация
                if (res.status === 200 && typeof res.data === 'string' && !res.data.includes('<table')) {
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
                    if (res.data.includes('<table')) {
                        log.info(`[Schedule] ✅ Получено расписание через прокси ${proxy}`);
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

        throw new Error(`Не удалось скачать расписание после ${maxAttempts} попыток`);
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
    // СТАРЫЕ МЕТОДЫ (через Puppeteer) — используются в SyncService
    // ===========================

    get_faculty_list = async (browser) => {
        const pages = await browser.pages();
        const page = pages.length > 0 ? pages[0] : await browser.newPage();
        const domain = `${config.KSU_DOMAIN}`;
        try {
            await page.goto(`${domain}/login.php`, {waitUntil: 'domcontentloaded'});
            await page.waitForSelector('input', {timeout: 10 * 1000});
            await page.type('input[name="login"]', config.KSU_LOGIN);
            await page.type('input[name="password"]', config.KSU_PASSWORD);
            await page.click('input[type="submit"]');

            await page.waitForTimeout(1000);

            await page.goto(`${domain}`, {waitUntil: "domcontentloaded"});

            await page.waitForSelector("select");
            const webFacultyList = await page.evaluate((selector) => {
                const select = document.querySelector(selector);
                return Array.from(select.options).map((option) => option.text);
            }, 'select[name="Login"]');
            const faculties_data = webFacultyList.map((faculty, index) => {
                return {name: faculty, id: index};
            });

            await page.select('select[name="Login"]', webFacultyList[0]);
            await page.click('input[type="submit"]');

            await page.waitForSelector("center center p");
            const cookies = await page.cookies();
            const auth_cookie = await cookies.find(cookie => cookie.name === "PHPSESSID");

            return {faculties_data, auth_cookie};
        } catch (e) {
            const path = `logs/error_auth_${Date.now()}.png`;
            await page.screenshot({
                path,
            }).catch(e => console.log("Не получилось заскринить ошибочку" + e.message));
            await page.close();
            throw new Error("Ошибка при авторизации. Ошибку заскринил" + e.message);
        }
    }

    get_program_list_by_facultyId = async (browser, faculties_data, id) => {
        const page = await browser.newPage();
        try {
            await page.goto(`${config.KSU_DOMAIN}/`);

            await page.select('select[name="Login"]', faculties_data[id].name);
            await page.click('input[type="submit"]');

            await page.waitForSelector('a.genric-btn');

            const programs = await page.evaluate((facultyId) => {
                const links = document.querySelectorAll('a.genric-btn');
                const facultyName = document.querySelector("div.wrap p").textContent.replace("Факультет: ", "");
                return Array.from(links)
                    .filter((link) => link.getAttribute("href").includes("grupps"))
                    .map((link) => {
                        return {
                            name: String(link.textContent.trim()),
                            href: String(link.getAttribute('href')),
                            id: Number(link.getAttribute('href').split("=")[1]),
                            facultyId,
                            facultyName
                        };
                    });
            }, id);
            await page.close();
            return programs;
        } catch (e) {
            const path = `logs/error_${Date.now()}.png`;
            await page.screenshot({path});
            await page.close();
            throw new Error("Ошибка при получении программ. Ошибку заскринил." + e.message);
        }
    }

    get_group_list_by_programId = async (browser, id) => {
        const page = await browser.newPage();
        try {
            await page.goto(`${config.KSU_DOMAIN}/grupps1.php?id=${id}`);

            await page.waitForSelector("table");

            let groups = await page.$$eval('tbody tr:not(:first-child)', (rows, programId) => {
                return rows.map((row) => {
                    const name = row.querySelector('td a').textContent.trim();
                    const id = Number(row.querySelector('td a').getAttribute('href').match(/id=(\d+)/)[1]);
                    const href = row.querySelector('td a').getAttribute('href');
                    const language = href.match(/Otdel=([^&]+)/)[1];
                    const age = Number(href.match(/Kurs=(\d+)/)[1]);
                    const studentCount = href.match(/Stud=(\d+)/)[1];

                    return {
                        name,
                        id,
                        href,
                        language,
                        age,
                        studentCount,
                        programId
                    };
                });
            }, id);
            await page.close();
            return groups;
        } catch (e) {
            const path = `logs/error_${Date.now()}.png`;
            await page.screenshot({path});
            await page.close();
            throw new Error("Ошибка при получении групп. Ошибку заскринил." + e.message);
        }
    }
}

export default new ScheduleService();