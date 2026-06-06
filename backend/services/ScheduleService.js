import log from "../logging/logging.js";
import HtmlService from "./HtmlService.js";
import config from "../config.js";
import BrowserController from "../controllers/BrowserController.js";
import * as cheerio from "cheerio";

export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

class ScheduleService {

    get_faculty_list = async (axiosClient) => {
        try {
            const authData = new URLSearchParams();
            authData.append('login', config.KSU_LOGIN);
            authData.append('password', config.KSU_PASSWORD);

            const res1 = await axiosClient.post('/login.php', authData.toString(), {
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                maxRedirects: 0,
                validateStatus: status => status >= 200 && status < 400
            });

            const setCookie = res1.headers['set-cookie'];
            let auth_cookie = '';
            if (setCookie) {
                const sessionCookie = setCookie.find(c => c.includes('PHPSESSID'));
                if (sessionCookie) {
                    auth_cookie = sessionCookie.split(';')[0];
                }
            }
            if (!auth_cookie) throw new Error("Не удалось получить PHPSESSID из заголовков ответа");

            const res2 = await axiosClient.get('/', { headers: { Cookie: auth_cookie } });
            const $ = cheerio.load(res2.data);
            
            const faculties_data = [];
            $('select[name="Login"] option').each((i, el) => {
                faculties_data.push({ name: $(el).text(), id: i });
            });

            if (faculties_data.length > 0) {
                const facData = new URLSearchParams();
                facData.append('Login', faculties_data[0].name);
                await axiosClient.post('/index.php?x', facData.toString(), {
                    headers: { 
                        Cookie: auth_cookie,
                        'Content-Type': 'application/x-www-form-urlencoded' 
                    }
                });
            }

            return { faculties_data, auth_cookie };
        } catch (e) {
            throw new Error("Ошибка при HTTP авторизации: " + e.message)
        }
    }

    get_program_list_by_facultyId = async (axiosClient, faculties_data, id) => {
        try {
            const facData = new URLSearchParams();
            facData.append('Login', faculties_data[id].name);
            
            await axiosClient.post('/index.php?x', facData.toString(), {
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
            });
            const res = await axiosClient.get('/stud.php');
            const $ = cheerio.load(res.data);

            const facultyName = $("div.wrap p").text().replace("Факультет: ", "").trim();

            const programs = [];
            $('a.genric-btn').each((i, el) => {
                const href = $(el).attr('href') || "";
                if (href.includes("grupps")) {
                    const progIdMatch = href.split("=")[1];
                    if (progIdMatch) {
                        programs.push({
                            id: Number(progIdMatch),
                            name: String($(el).text().trim()),
                            href: String(href),
                            facultyId: id,
                            facultyName
                        });
                    }
                }
            });
            return programs;
        } catch (e) {
            throw new Error("Ошибка при получении программ (HTTP): " + e.message)
        }
    }

    get_group_list_by_programId = async (axiosClient, id) => {
        try {
            const res = await axiosClient.get(`/grupps1.php?id=${id}`);
            const $ = cheerio.load(res.data);
            
            const groups = [];
            $('table tbody tr:not(:first-child)').each((i, row) => {
                const link = $(row).find('td a');
                if (link.length > 0) {
                    const name = link.text().trim();
                    const href = link.attr('href');
                    const idMatch = href.match(/id=(\d+)/);
                    const langMatch = href.match(/Otdel=([^&]+)/);
                    const ageMatch = href.match(/Kurs=(\d+)/);
                    const studMatch = href.match(/Stud=(\d+)/);

                    if (idMatch && langMatch && ageMatch && studMatch) {
                        groups.push({
                            name,
                            id: Number(idMatch[1]),
                            href,
                            language: decodeURIComponent(langMatch[1]),
                            age: Number(ageMatch[1]),
                            studentCount: studMatch[1],
                            programId: id
                        });
                    }
                }
            });
            return groups;
        } catch (e) {
            throw new Error("Ошибка при получении групп (HTTP): " + e.message)
        }
    }

    get_schedule_by_groupId = async (id, language, attemption = 1) => {

        function removeBrTags(text) {
            if (text.includes('<br>')) {
                return removeBrTags(text.replace('<br>', '\n'));
            } else {
                return text;
            }
        }

        try {
            const axiosClient = BrowserController.axiosClient;
            if (!axiosClient) throw new Error("HTTP клиент не инициализирован");

            const url = encodeURI(`/view1.php?id=${id}&Otdel=${language}`);
            const res = await axiosClient.get(url, {timeout: 60000});
            const $ = cheerio.load(res.data);

            const isForbidden = $('h1').text().includes("Forbidden");
            if (isForbidden) {
                if (attemption >= 10) throw new Error("Forbidden even after 10 attempts");
                log.warn("(варн временный) Нас забанило, перезапускаю сессию (HTTP)!")
                await BrowserController.auth()
                return await this.get_schedule_by_groupId(id, language, attemption + 1)
            }

            const isTableNotExists = $('table').length === 0;
            if (isTableNotExists) {
                if (attemption >= 10) throw new Error("Table not exists even after 10 attempts");
                await sleep(5000)
                log.info("table not exists handler, attemption = " + attemption)
                await BrowserController.auth()
                return await this.get_schedule_by_groupId(id, language, attemption + 1)
            }

            const tableHTML = $.html($('table'));
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

                schedule.push(daily_schedule)
            }

            for (const daily_schedule of schedule) {
                for (const subject of daily_schedule.subjects) {
                    if (subject.subject === "\n") {
                        if (attemption >= 10) throw new Error("Bad schedule parse even after 10 attempts");
                        log.warn("[test] Вижу кривое расписание на сайте КарГУ. Делаю рестарт (HTTP). Group: " + id)
                        await BrowserController.auth()
                        log.warn("[test] Делаю рекурсию для получения расписания повторно. ")
                        return await this.get_schedule_by_groupId(id, language, attemption + 1)
                    }
                }
            }

            return schedule
        } catch (e) {
            log.error(`[get_schedule_by_groupId] Ошибка (попытка ${attemption}): ${e.message}`);
            if (attemption < 10) {
                log.info(`[get_schedule_by_groupId] Запрашиваю новую авторизацию из-за ошибки сети/парсинга...`);
                await BrowserController.auth();
                await sleep(1000);
                return await this.get_schedule_by_groupId(id, language, attemption + 1)
            } else {
                throw new Error("Ошибка при получении студенческого расписания (HTTP): " + e.message)
            }
        }
    }
}

export default new ScheduleService()