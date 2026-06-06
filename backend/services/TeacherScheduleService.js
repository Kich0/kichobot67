import BrowserController from "../controllers/BrowserController.js";
import ApiError from "../exceptions/apiError.js";
import HtmlService from "./HtmlService.js";
import log from "../logging/logging.js";
import {sleep} from "./ScheduleService.js";
import BrowserService from "./BrowserService.js";
import config from "../config.js";

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
        const page = await BrowserController.browser.newPage()
        try {
            await page.goto(`${config.KSU_DOMAIN}/kafedra.php`)

            const linksSelector = 'table a';

            await page.waitForSelector(linksSelector)

            const links = await page.$$(linksSelector)
            const linkObjects = [];

            for (const link of links) {
                const name = await (await link.getProperty('textContent')).jsonValue();
                const href = await (await link.getProperty('href')).jsonValue();
                const id = await getQueryParam(href, "IdKaf")

                linkObjects.push({name, href, id})
            }


            await page.close()

            return linkObjects
        } catch (e) {
            await page.close().catch(err => log.error("Ошибка при закрытии страницы в get_departments_list: " + err.message))
            throw e
        }
    }

    async get_teachers_list(departmentId) {
        const page = await BrowserController.browser.newPage()
        try {
            await page.goto(`${config.KSU_DOMAIN}/report_prep.php?d=1&IdKaf=${departmentId}`)

            const tableSelector = 'table'

            await page.waitForSelector(tableSelector)
            const tables = await page.$$(tableSelector);

            const secondTable = tables[1];

            if (!secondTable) {
                throw ApiError.ServiceUnavailable("Не получилось получить вторую табличку на странице кафдеры. в ней хранится список преподов")
            }

            const links = await secondTable.$$('a');

            const linkObjects = [];

            for (const link of links) {
                const name = await (await link.getProperty('textContent')).jsonValue();
                const href = await (await link.getProperty('href')).jsonValue();
                const id = await getQueryParam(href, 'IdPrep')
                if (name === '- ') {
                    continue
                }
                linkObjects.push({name, href, id, departmentId})
            }

            await page.close()

            return linkObjects
        } catch (e) {
            await page.close().catch(err => log.error("Ошибка при закрытии страницы в get_teachers_list: " + err.message))
            throw e
        }
    }

    async get_teacher_schedule(id, attemption = 1) {
        const page = await BrowserController.browser.newPage()
        try {
            await page.goto(`${config.KSU_DOMAIN}/report_prep1.php?IdPrep=${id}`, {timeout:7000})

            await page.waitForSelector("body", {timeout: 2000})

            const isForbidden = await page.evaluate(() => {
                const h1 = document.querySelector(`h1`);
                return h1 ? h1.textContent.includes("Forbidden") : false
            });

            if (isForbidden){
                log.warn("(варн временный) Нас забанило, перезапускаю браузер!")
                await BrowserService.restartBrowser()
                return await this.get_teacher_schedule(id, ++attemption)
            }

            const isTableNotExists = await page.evaluate(() => {
                return !document.querySelector('table');
            });

            if (isTableNotExists){
                await sleep(10000)
                log.info("teacher table not exists handler, attemption = " + attemption)
                await page.close()
                await BrowserController.auth()
                return await this.get_teacher_schedule(id, ++attemption)
            }


            const tableHTML = await page.evaluate((selector) => {
                const table = document.querySelector(selector);
                return table ? table.outerHTML : null;
            }, "table");

            await page.close()

            const tableData = HtmlService.htmlTableToJson(tableHTML)

            const schedule = []
            for (let i = 1; i < tableData.length; i++) {
                const dailySchedule = {}
                dailySchedule['day'] = tableData[i][0]
                const groups = []
                for (let j = 1; j < tableData[i].length; j++) {
                    const time = tableData[0][j]
                    let group = tableData[i][j]
                    if (group === '-') {
                        group = ""
                    }
                    groups.push({
                        time, group
                    })
                }

                const firstGroupIndex = groups.findIndex(item => item.group !== '');
                let trimmedGroups = []
                if (firstGroupIndex !== -1){
                    const lastGroupIndex = groups.reverse().findIndex(item => item.group !== '');

                    groups.reverse();

                    trimmedGroups = groups.slice(firstGroupIndex, groups.length - lastGroupIndex);
                }else{
                    trimmedGroups = []
                }

                dailySchedule['groups'] = trimmedGroups
                schedule.push(dailySchedule)
            }

            return schedule
        } catch (e) {
            if (attemption < 1) {
                await page.close().catch(e => console.log(e))
                await sleep(1000);
                return await this.get_teacher_schedule(id, ++attemption)
            } else {
                // const path = `logs/error_${Date.now()}.png`
                // await page.screenshot({path, fullPage: true});
                await page.close().catch(e => console.log(e))
                throw new Error("Ошибка при получении преподского расписания. Ошибку заскринил." + e.message)
            }
        }
    }
}

export default new TeacherScheduleService()