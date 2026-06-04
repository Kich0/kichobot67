import log from "../logging/logging.js";
import HtmlService from "./HtmlService.js";
import config from "../config.js";
import BrowserController from "../controllers/BrowserController.js";
import BrowserService from "./BrowserService.js";

export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

class ScheduleService {

    get_faculty_list = async (browser) => {
        const page = await browser.newPage();
        const domain = `${config.KSU_DOMAIN}`
        try {
            await page.goto(`${domain}/login.php`, {waitUntil: 'domcontentloaded'});
            // Дождемся, когда загрузится содержимое сайта
            await page.waitForSelector('input', {timeout: 10 * 1000});
            await page.type('input[name="login"]', config.KSU_LOGIN)
            await page.type('input[name="password"]', config.KSU_PASSWORD)
            await page.click('input[type="submit"]')

            await page.waitForTimeout(1000)

            await page.goto(`${domain}`, {waitUntil: "domcontentloaded"});

            await page.waitForSelector("select")
            // Получаем список опций селекта
            const webFacultyList = await page.evaluate((selector) => {
                const select = document.querySelector(selector);
                return Array.from(select.options).map((option) => option.text);
            }, 'select[name="Login"]');
            const faculties_data = webFacultyList.map((faculty, index) => {
                return {name: faculty, id: index}
            })

            await page.select('select[name="Login"]', webFacultyList[0]);
            await page.click('input[type="submit"]')

            await page.waitForSelector("center center p")
            const cookies = await page.cookies()
            const auth_cookie = await cookies.find(cookie => cookie.name === "PHPSESSID");

            await page.close()

            return {faculties_data, auth_cookie}
        } catch (e) {
            const path = `logs/error_auth_${Date.now()}.png`
            await page.screenshot({
                path,
                fullPage: true
            }).catch(e => console.log("Не получилось заскринить ошибочку" + e.message));
            await page.close()
            throw new Error("Ошибка при авторизации. Ошибку заскринил" + e.message)
        }
    }

    get_program_list_by_facultyId = async (browser, faculties_data, id) => {
        const page = await browser.newPage();
        try {
            await page.goto(`${config.KSU_DOMAIN}/`)

            await page.select('select[name="Login"]', faculties_data[id].name);
            await page.click('input[type="submit"]')

            await page.waitForSelector('a.genric-btn');

            const programs = await page.evaluate((facultyId) => {
                const links = document.querySelectorAll('a.genric-btn');
                const facultyName = document.querySelector("div.wrap p").textContent.replace("Факультет: ", "")
                // Преобразуем NodeList в массив и извлекаем данные
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
            await page.close()
            return programs
        } catch (e) {
            const path = `logs/error_${Date.now()}.png`
            await page.screenshot({path, fullPage: true});
            await page.close()
            throw new Error("Ошибка при получении программ. Ошибку заскринил." + e.message)
        }

    }

    get_group_list_by_programId = async (browser, id) => {
        const page = await browser.newPage();
        try {
            await page.goto(`${config.KSU_DOMAIN}/grupps1.php?id=${id}`)

            await page.waitForSelector("table")

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
            await page.close()
            return groups
        } catch (e) {
            const path = `logs/error_${Date.now()}.png`
            await page.screenshot({path, fullPage: true});
            await page.close()
            throw new Error("Ошибка при получении групп. Ошибку заскринил." + e.message)
        }

    }

    get_schedule_by_groupId = async (id, language, attemption = 1) => {
        if (attemption > 2) {
            throw new Error("Ошибка при получении расписания. слишком много рекурсий")
        }

        function removeBrTags(text) {
            if (text.includes('<br>')) {
                return removeBrTags(text.replace('<br>', '\n'));
            } else {
                return text;
            }
        }

        const page = await BrowserController.browser.newPage();
        try {
            await page.goto(`${config.KSU_DOMAIN}/view1.php?id=${id}&Otdel=${language}`, {waitUntil: "domcontentloaded", timeout: 7000})

            await page.waitForSelector("body", {timeout: 2000})

            const isForbidden = await page.evaluate(() => {
                const h1 = document.querySelector(`h1`);
                return h1 ? h1.textContent.includes("Forbidden") : false
            });

            if (isForbidden) {
                log.warn("(варн временный) Нас забанило, перезапускаю браузер!")
                await BrowserService.restartBrowser()
                return await this.get_schedule_by_groupId(id, language, ++attemption)
            }

            const isTableNotExists = await page.evaluate(() => {
                return !document.querySelector('table');
            });

            if (isTableNotExists) {
                await sleep(10000)
                log.info("table not exists handler, attemption = " + attemption)
                await page.close()
                await BrowserController.auth()
                return await this.get_schedule_by_groupId(id, language, ++attemption)
            }

            const tableHTML = await page.evaluate((selector) => {
                const table = document.querySelector(selector);
                return table ? table.outerHTML : null;
            }, "table");

            await page.close()

            const tableData = HtmlService.htmlTableToJson(tableHTML)

            const headers = tableData.shift();
            const schedule_data = tableData.map(row => {
                const obj = {};
                headers.forEach((header, index) => {
                    obj[header] = row[index];
                });
                return obj;
            });

            let days_list = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
            if (language === "каз") {
                days_list = ['Дүйсенбі', 'Сейсенбі', 'Сәрсенбі', 'Бейсенбі', 'Жұма', 'Сенбі']
            }

            let schedule = []
            let item_number = 0
            for (let i = 0; i < 6; i++) {
                let daily_subjects = []
                let day = ''
                for (let j = 0; j < 13; j++) {
                    let item = schedule_data[item_number]
                    if (j === 0) {
                        day = days_list[i]
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
                        values[2] = ""
                    }
                    daily_subjects.push({
                        time: values[1],
                        subject: removeBrTags(values[2])
                    })

                    item_number += 1
                }

                const firstSubjectIndex = daily_subjects.findIndex(item => item.subject !== '');
                let trimmedDailySubjects = []
                if (firstSubjectIndex !== -1) {
                    const lastSubjectIndex = daily_subjects.reverse().findIndex(item => item.subject !== '');

                    daily_subjects.reverse();

                    trimmedDailySubjects = daily_subjects.slice(firstSubjectIndex, daily_subjects.length - lastSubjectIndex);
                } else {
                    trimmedDailySubjects = []
                }

                let daily_schedule = {
                    day,
                    subjects: trimmedDailySubjects
                }


                // const lastSubjectIndex = daily_schedule.reverse().findIndex(item => item.subject !== '');
                //
                // daily_schedule.reverse();
                //
                // const trimmedDailySchedule = daily_schedule.slice(firstSubjectIndex, daily_schedule.length - lastSubjectIndex);


                schedule.push(daily_schedule)
            }

            for (const daily_schedule of schedule) {
                for (const subject of daily_schedule.subjects) {
                    if (subject.subject === "\n") {
                        log.warn("[test] Вижу кривое расписание в ксу хелпере. Запускаю рестарт браузера. Group: " + id)
                        await BrowserService.restartBrowser()
                        log.warn("[test] Делаю рекурсию для получения расписания повторно. ")
                        return await this.get_schedule_by_groupId(id, language, ++attemption)
                    }
                }
            }

            return schedule
        } catch (e) {
            if (attemption < 1) {
                await page.close().catch(e => console.log(e))
                await sleep(1000);
                return await this.get_schedule_by_groupId(id, language, ++attemption)
            } else {
                const path = `logs/error_studentSchedule_${Date.now()}.png`
                await page.screenshot({
                    path,
                    fullPage: true
                }).catch(e => console.log("Не получиось заскринить ошибку( " + e.message))
                await page.close().catch(e => console.log(e))
                throw new Error("Ошибка при получении студенческого расписания. Ошибку заскринил." + e.message)
            }
        }
    }
}

export default new ScheduleService()