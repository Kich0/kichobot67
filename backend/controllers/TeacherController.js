import log from "../logging/logging.js";
import BrowserController from "./BrowserController.js";

class TeacherController{
    async get_all_teachers(req, res, next){
        let all_teachers = []
        const page = await BrowserController.browser.newPage();
        try{
            await page.goto('https://buketov.edu.kz/ru/page/faculty');

            const faculties = (await page.$$('h4 a')).slice(0, 12);
            console.log(faculties)
            for (const faculty of faculties) {
                const facultyHrefHandle = await faculty.getProperty('href');
                const facultyHref = await facultyHrefHandle.jsonValue();
                const facultyName = await faculty.evaluate(node => node.textContent);

                const facultyPage = await BrowserController.browser.newPage();
                try {
                    await facultyPage.goto(facultyHref);
                    await facultyPage.waitForSelector('div.dropdown a');

                    const departments = await facultyPage.$$('div.dropdown a');
                    console.log(departments)
                    for (const department of departments) {
                        const departmentHrefHandle = await department.getProperty('href');
                        const departmentHref = await departmentHrefHandle.jsonValue();
                        const departmentName = await department.evaluate(node => node.textContent);

                        const teachersPage = await BrowserController.browser.newPage();
                        try {
                            await teachersPage.goto(departmentHref);
                            const teachers = await teachersPage.$$(`#hiddenDiv3 div p a`);
                            console.log(teachers)
                            for (const teacher of teachers) {
                                const teacherHrefHandle = await teacher.getProperty('href');
                                const teacherHref = await teacherHrefHandle.jsonValue();
                                const teacherName = await teacher.evaluate(node => node.textContent);
                                const teacherData = {
                                    name:teacherName, faculty:facultyName, department:departmentName, href:teacherHref
                                }
                                all_teachers.push(teacherData)
                                console.log(teacherData)
                            }
                        } finally {
                            await teachersPage.close().catch(e => log.error("Ошибка при закрытии teachersPage: " + e.message))
                        }
                    }
                } finally {
                    await facultyPage.close().catch(e => log.error("Ошибка при закрытии facultyPage: " + e.message))
                }
            }
            await page.close()
            return res.json(all_teachers)
        }catch (e) {
            await page.close().catch(err => log.error("Ошибка при закрытии главной страницы: " + err.message))
            log.error("Ошибка при получении списка всех преподавателей")
            console.error(e)
            next(e)
        }
    }
}

export default new TeacherController()