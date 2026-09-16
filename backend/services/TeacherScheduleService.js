import KsuAuthService from "./KsuAuthService.js";
import FreeProxyService from "./FreeProxyService.js";
import HtmlService from "./HtmlService.js";
import log from "../logging/logging.js";
import {sleep} from "./ScheduleService.js";
import config from "../config.js";
import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import * as cheerio from "cheerio";
import BuketovApiService from "../../bot/services/buketovApiService.js";
import ScheduleApiAdapter from "../../bot/services/scheduleApiAdapter.js";
import { Teacher } from "../../bot/models/teacher.js";

const FETCH_TIMEOUT = 12000; // 12 сек

class TeacherScheduleService {
    /**
     * Общий метод для скачивания HTML-страницы с КарГУ через axios.
     * Стратегия: сначала напрямую, потом через прокси.
     */
    async _fetchPage(url, maxAttempts = 5) {
        const cookie = await KsuAuthService.getCookie();

        // Попытка 1: напрямую
        try {
            const res = await axios.get(url, {
                timeout: FETCH_TIMEOUT,
                maxRedirects: 0,
                validateStatus: () => true,
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Cookie': cookie
                }
            });

            if (res.status === 200 && typeof res.data === 'string' && !res.data.includes('Cloudflare')) {
                return res.data;
            }

            if (res.status === 302 || res.status === 301) {
                KsuAuthService.invalidate();
            }
        } catch (e) {
            log.warn(`[TeacherSchedule] Прямой запрос не удался: ${e.message}`);
        }

        // Попытки через прокси
        let currentCookie = cookie;
        for (let i = 1; i <= maxAttempts; i++) {
            const proxy = FreeProxyService.getNextProxy();
            if (!proxy) {
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
                    timeout: FETCH_TIMEOUT,
                    maxRedirects: 0,
                    validateStatus: () => true,
                    headers: {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Cookie': currentCookie
                    }
                });

                if (res.status === 302 || res.status === 301) {
                    KsuAuthService.invalidate();
                    currentCookie = await KsuAuthService.getCookie();
                    continue;
                }

                if ([400, 403, 502, 503, 504].includes(res.status)) {
                    FreeProxyService.markProxyDead(proxy);
                    continue;
                }

                if (res.status === 200 && typeof res.data === 'string') {
                    if (res.data.includes('Cloudflare') || res.data.includes('Just a moment')) {
                        FreeProxyService.markProxyDead(proxy);
                        continue;
                    }
                    if (res.data.includes('Forbidden')) {
                        FreeProxyService.markProxyDead(proxy);
                        continue;
                    }
                    return res.data;
                }
            } catch (e) {
                const msg = e.message || '';
                if (msg.includes('ECONNRESET') || msg.includes('ETIMEDOUT') || msg.includes('ECONNREFUSED') || msg.includes('ERR_TUNNEL')) {
                    FreeProxyService.markProxyDead(proxy);
                }
                log.warn(`[TeacherSchedule] Прокси ${proxy}: ошибка — ${msg}`);
            }
        }

        throw new Error(`Не удалось скачать страницу ${url} после ${maxAttempts} попыток`);
    }

    async get_departments_list() {
        const url = `${config.KSU_DOMAIN}/kafedra.php`;
        const html = await this._fetchPage(url);

        const $ = cheerio.load(html);
        const linkObjects = [];

        $('table a').each((_, el) => {
            const name = $(el).text().trim();
            const href = $(el).attr('href') || '';
            const idMatch = href.match(/IdKaf=(\d+)/);
            const id = idMatch ? idMatch[1] : null;
            if (id) {
                linkObjects.push({ name, href, id });
            }
        });

        return linkObjects;
    }

    async get_teachers_list(departmentId) {
        const url = `${config.KSU_DOMAIN}/report_prep.php?d=1&IdKaf=${departmentId}`;
        const html = await this._fetchPage(url);

        const $ = cheerio.load(html);
        const tables = $('table');
        const secondTable = tables.eq(1);

        if (secondTable.length === 0) {
            throw new Error("Не получилось получить вторую табличку на странице кафедры");
        }

        const linkObjects = [];
        secondTable.find('a').each((_, el) => {
            const name = $(el).text().trim();
            const href = $(el).attr('href') || '';
            const idMatch = href.match(/IdPrep=(\d+)/);
            const id = idMatch ? idMatch[1] : null;

            if (name === '- ' || !id) return;

            linkObjects.push({ name, href, id, departmentId });
        });

        return linkObjects;
    }

    async get_teacher_schedule(id) {
        // 1. Приоритетный путь: официальный API КарУ
        try {
            const teacher = await Teacher.findOne({ id }).catch(() => null);
            if (teacher && teacher.name) {
                const apiData = await BuketovApiService.getTeacherSchedule(teacher.name);
                if (apiData && Array.isArray(apiData.records) && apiData.records.length > 0) {
                    return ScheduleApiAdapter.adaptTeacherSchedule(apiData.records);
                }
            }
        } catch (apiErr) {
            log.warn(`[TeacherSchedule] Ошибка BuketovApiService для преподавателя id=${id}: ${apiErr.message}`);
        }

        // 2. Резервный Fallback: кэш из базы данных MongoDB
        try {
            const { TeacherSchedule } = await import("../../bot/models/teacherSchedule.js");
            const dbDoc = await TeacherSchedule.findOne({ teacherId: id }).catch(() => null);
            if (dbDoc && Array.isArray(dbDoc.schedule) && dbDoc.schedule.length > 0) {
                log.info(`[TeacherSchedule] Использован кэш MongoDB для преподавателя id=${id}`);
                return dbDoc.schedule;
            }
        } catch (dbErr) {
            log.warn(`[TeacherSchedule] Ошибка чтения кэша MongoDB: ${dbErr.message}`);
        }

        // 3. Дополнительный fallback: старый парсер HTML
        try {
            const url = `${config.KSU_DOMAIN}/report_prep1.php?IdPrep=${id}`;
            const html = await this._fetchPage(url);
            const $ = cheerio.load(html);
            const table = $('table').first();
            if (table.length > 0) {
                const tableHTML = $.html(table);
                const tableData = HtmlService.htmlTableToJson(tableHTML);
                const schedule = [];
                for (let i = 1; i < tableData.length; i++) {
                    const dailySchedule = {};
                    dailySchedule['day'] = tableData[i][0];
                    const groups = [];
                    for (let j = 1; j < tableData[i].length; j++) {
                        const time = tableData[0][j];
                        let group = tableData[i][j];
                        if (group === '-') group = "";
                        groups.push({ time, group });
                    }
                    const firstGroupIndex = groups.findIndex(item => item.group !== '');
                    let trimmedGroups = [];
                    if (firstGroupIndex !== -1) {
                        const lastGroupIndex = groups.reverse().findIndex(item => item.group !== '');
                        groups.reverse();
                        trimmedGroups = groups.slice(firstGroupIndex, groups.length - lastGroupIndex);
                    }
                    dailySchedule['groups'] = trimmedGroups;
                    schedule.push(dailySchedule);
                }
                return schedule;
            }
        } catch (fallbackErr) {
            log.warn(`[TeacherSchedule] Fallback парсер HTML не удался: ${fallbackErr.message}`);
        }

        throw new Error(`Не удалось получить расписание преподавателя id=${id}`);
    }
}

export default new TeacherScheduleService();