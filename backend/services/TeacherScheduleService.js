import BrowserController from "../controllers/BrowserController.js";
import ApiError from "../exceptions/apiError.js";
import HtmlService from "./HtmlService.js";
import log from "../logging/logging.js";
import {sleep} from "./ScheduleService.js";
import config from "../config.js";
import * as cheerio from "cheerio";

function getQueryParam(url, paramName) {
    const urlParts = url.split('?');
    const queryString = urlParts[1] || '';
    const queryParams = {};

    queryString.split('&').forEach((param) => {
        const [key, value] = param.split('=');
        queryParams[key] = decodeURIComponent(value);
    });

    return queryParams[paramName] || null;
}

class TeacherScheduleService {
    async get_departments_list() {
        try {
            const axiosClient = BrowserController.axiosClient;
            const res = await axiosClient.get('/kafedra.php');
            const $ = cheerio.load(res.data);

            const linkObjects = [];
            $('table a').each((i, el) => {
                const name = $(el).text();
                const href = $(el).attr('href');
                const id = getQueryParam(href, "IdKaf");
                linkObjects.push({name, href, id});
            });

            return linkObjects;
        } catch (e) {
            log.error("Ошибка при получении списка кафедр (HTTP): " + e.message);
            throw e;
        }
    }

    async get_teachers_list(departmentId) {
        try {
            const axiosClient = BrowserController.axiosClient;
            const res = await axiosClient.get(`/report_prep.php?d=1&IdKaf=${departmentId}`);
            const $ = cheerio.load(res.data);

            const tables = $('table');
            const secondTable = tables.eq(1);

            if (secondTable.length === 0) {
                throw ApiError.ServiceUnavailable("Не получилось получить вторую табличку на странице кафедры. в ней хранится список преподов");
            }

            const linkObjects = [];
            secondTable.find('a').each((i, el) => {
                const name = $(el).text();
                const href = $(el).attr('href');
                const id = getQueryParam(href, 'IdPrep');
                if (name !== '- ') {
                    linkObjects.push({name, href, id, departmentId});
                }
            });

            return linkObjects;
        } catch (e) {
            log.error("Ошибка при получении списка преподавателей (HTTP): " + e.message);
            throw e;
        }
    }

    async get_teacher_schedule(id, attemption = 1) {
        try {
            const axiosClient = BrowserController.axiosClient;
            const res = await axiosClient.get(`/report_prep1.php?IdPrep=${id}`, {timeout: 7000});
            const $ = cheerio.load(res.data);

            const isForbidden = $('h1').text().includes("Forbidden");
            if (isForbidden){
                if (attemption >= 3) throw new Error("Forbidden even after 3 attempts");
                log.warn("(варн временный) Нас забанило, перезапускаю сессию (HTTP)!");
                await BrowserController.auth();
                return await this.get_teacher_schedule(id, attemption + 1);
            }

            const isTableNotExists = $('table').length === 0;
            if (isTableNotExists){
                if (attemption >= 3) throw new Error("Table not exists even after 3 attempts");
                await sleep(5000);
                log.info("teacher table not exists handler (HTTP), attemption = " + attemption);
                await BrowserController.auth();
                return await this.get_teacher_schedule(id, attemption + 1);
            }

            const tableHTML = $.html($('table'));
            const tableData = HtmlService.htmlTableToJson(tableHTML);

            const schedule = [];
            for (let i = 1; i < tableData.length; i++) {
                const dailySchedule = {};
                dailySchedule['day'] = tableData[i][0];
                const groups = [];
                for (let j = 1; j < tableData[i].length; j++) {
                    const time = tableData[0][j];
                    let group = tableData[i][j];
                    if (group === '-') {
                        group = "";
                    }
                    groups.push({time, group});
                }

                const firstGroupIndex = groups.findIndex(item => item.group !== '');
                let trimmedGroups = [];
                if (firstGroupIndex !== -1){
                    const lastGroupIndex = groups.reverse().findIndex(item => item.group !== '');
                    groups.reverse();
                    trimmedGroups = groups.slice(firstGroupIndex, groups.length - lastGroupIndex);
                } else {
                    trimmedGroups = [];
                }

                dailySchedule['groups'] = trimmedGroups;
                schedule.push(dailySchedule);
            }

            return schedule;
        } catch (e) {
            log.error(`[get_teacher_schedule] Ошибка (попытка ${attemption}): ${e.message}`);
            if (attemption < 3) {
                log.info(`[get_teacher_schedule] Запрашиваю новую авторизацию из-за ошибки сети/парсинга...`);
                await BrowserController.auth();
                await sleep(1000);
                return await this.get_teacher_schedule(id, attemption + 1);
            } else {
                throw new Error("Ошибка при получении преподского расписания (HTTP): " + e.message);
            }
        }
    }
}

export default new TeacherScheduleService();