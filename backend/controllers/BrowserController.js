import config from "../config.js";
import puppeteer from "puppeteer";
import ScheduleService from "../services/ScheduleService.js";
import log from "../logging/logging.js";
import BrowserService from "../services/BrowserService.js";
import FreeProxyService from "../services/FreeProxyService.js";
// import ping from "ping";
// import ApiError from "../exceptions/apiError.js";
// import axios from "axios";


class BrowserController {
    browser;
    auth_cookie;
    faculties_data;
    isAuthing;
    isRecovering; // Флаг для блокировки параллельных попыток восстановления
    recoveryTimeout; // Таймер для автосброса флага восстановления
    isLaunching; // Флаг для блокировки параллельных запусков браузера
    launchTimeout; // Таймер для автосброса флага запуска

    constructor() {
        this.isRecovering = false;
        this.recoveryTimeout = null;
        this.isLaunching = false;
        this.launchTimeout = null;
        if (config.START_BROWSER) {
            this.isAuthing = true;
            this.launchBrowser().then(() => log.info("Браузер запущен"))
        }
    }

    allChecksCall = async (req, res, next) => {
        try {
            // Блокируем входящие запросы если идёт запуск браузера
            if (this.isLaunching) {
                throw new Error("Идёт запуск браузера, попробуйте через несколько секунд")
            }

            if (!this.browser?.isConnected()) {
                await this.launchBrowser();
            }

            // Блокируем входящие запросы если идёт авторизация или восстановление
            if (this.isAuthing) {
                throw new Error("Идёт авторизация в КСУ, попробуйте через несколько секунд")
            }

            if (this.isRecovering) {
                throw new Error("Идёт восстановление соединения с КСУ, попробуйте через несколько секунд")
            }

            // Защита от утечки памяти - проверяем количество открытых страниц
            const pages = await this.browser.pages();
            const openPagesCount = pages.length;

            log.info(`[Memory Check] Открыто страниц: ${openPagesCount}`);

            // Если открыто более 20 страниц - закрываем все кроме первой и перезапускаем браузер
            if (openPagesCount > 20) {
                log.warn(`[Memory Protection] Обнаружено ${openPagesCount} открытых страниц! Закрываю все и перезапускаю браузер.`);

                // Закрываем все страницы кроме первой (about:blank)
                for (let i = 1; i < pages.length; i++) {
                    try {
                        await pages[i].close();
                    } catch (e) {
                        log.error(`Ошибка при закрытии страницы ${i}: ${e.message}`);
                    }
                }

                // Перезапускаем браузер для гарантии очистки памяти
                await this.browser?.close().catch(e => log.error("Ошибка при закрытии браузера: " + e.message));
                await this.launchBrowser();

                throw new Error("Браузер был перезапущен из-за утечки памяти. Попробуйте запрос снова.");
            }

            // Дополнительная проверка - если открыто 10-20 страниц, закрываем лишние
            if (openPagesCount > 10) {
                log.warn(`[Memory Warning] Открыто ${openPagesCount} страниц. Закрываю лишние.`);
                for (let i = 1; i < pages.length; i++) {
                    try {
                        await pages[i].close();
                    } catch (e) {
                        log.error(`Ошибка при закрытии страницы ${i}: ${e.message}`);
                    }
                }
            }

            next();
        } catch (e) {
            log.error("Ошибка в allChecksCall мидлваре(" + e.message, e)
            next(e)
        }
    }

    async launchBrowser() {
        // Если браузер уже запускается - ждём его запуска, не создаём новый
        if (this.isLaunching) {
            log.info("[Launch Lock] Браузер уже запускается, жду завершения...");
            // Ждём максимум 30 секунд пока запустится
            for (let i = 0; i < 60; i++) {
                await new Promise(resolve => setTimeout(resolve, 500));
                if (!this.isLaunching && this.browser?.isConnected()) {
                    log.info("[Launch Lock] Браузер запустился, продолжаю работу");
                    return;
                }
            }
            throw new Error("Таймаут ожидания запуска браузера (30 сек)");
        }

        this.isLaunching = true;
        log.info("[Launch Start] Начинаю запуск браузера");

        // Защита от зависания - если через 45 секунд флаг не сброшен, сбрасываем принудительно
        if (this.launchTimeout) {
            clearTimeout(this.launchTimeout);
        }
        this.launchTimeout = setTimeout(() => {
            if (this.isLaunching) {
                log.error("[Launch Timeout] Запуск браузера зависло больше 45 секунд, принудительно сбрасываю флаг!");
                this.isLaunching = false;
            }
        }, 45000);

        try {
            if (config.DEBUG) {
                this.browser = await puppeteer.launch({
                    headless: false,
                    args: ['--window-size=900,800', '--window-position=-10,0',],
                    ignoreHTTPSErrors: true,
                })
            } else {
                if (config.PROXY_LOGIN && config.USE_PROXY) {
                    this.browser = await puppeteer.launch({
                        headless: "new",
                        args: ["--no-sandbox", "--disable-local-file-access", `--proxy-server=${config.HTTP_PROXY}`],
                        // executablePath: '/usr/bin/google-chrome-stable',
                        ignoreHTTPSErrors: true,
                    })
                } else {
                    this.browser = await puppeteer.launch({
                        headless: "new",
                        args: [
                            "--no-sandbox", 
                            "--disable-local-file-access",
                            "--disable-setuid-sandbox",
                            "--disable-dev-shm-usage"
                        ],
                        // executablePath: '/usr/bin/google-chrome-stable',
                        ignoreHTTPSErrors: true,
                    })
                }
            }
            if (config.PROXY_LOGIN && config.USE_PROXY) {
                const page = await this.browser.newPage()
                await page.authenticate({username: config.PROXY_LOGIN, password: config.PROXY_PASSWORD});
                await page.goto('https://2ip.ru');
                await page.close()
                console.log("Прокси авторизован")
            }

            // Направляем первую страницу на домен КСУ сразу после запуска
            const pages = await this.browser.pages();
            if (pages.length > 0) {
                await pages[0].goto(config.KSU_DOMAIN).catch(e => log.error("Ошибка при переходе на главную страницу: " + e.message));
            }

            if (config.AUTO_KSU_AUTH) {
                await this.auth()
            }
            log.info("[Launch Success] Браузер успешно запущен");
        } catch (e) {
            log.error("[Launch Error] Ошибка при запуске браузера: " + e.message);
            throw new Error(e)
        } finally {
            if (this.launchTimeout) {
                clearTimeout(this.launchTimeout);
                this.launchTimeout = null;
            }
            this.isLaunching = false;
            log.info("[Launch End] Завершил процесс запуска браузера");
        }
    }

    async createOptimizedPage() {
        if (!this.browser?.isConnected()) {
            await this.launchBrowser();
        }
        const page = await this.browser.newPage();
        await page.setRequestInterception(true);
        page.on('request', (req) => {
            const resourceType = req.resourceType();
            if (['image', 'stylesheet', 'font', 'media'].includes(resourceType)) {
                req.abort().catch(() => {});
            } else {
                req.continue().catch(() => {});
            }
        });
        return page;
    }

    // need to fix this shit.
    async restartBrowser(req, res, next) {
        try {
            await BrowserService.restartBrowser()
            return res.json("Restarted")
        } catch (e) {
            next(e)
        }
    }

    async makeHtmlScreenShot(req, res, next) {
        const htmlCode = req.body
        console.log(htmlCode)
        try {
            const screenshotBuffer = await BrowserService.getScreenshotBufferByHtml(htmlCode)
            res.setHeader('Content-Type', 'image/png');
            res.setHeader('Content-Length', screenshotBuffer.length);

            res.send(screenshotBuffer);
        } catch (e) {
            next(e)
        }
    }

    async auth() {
        try {
            console.log("Начинаю авторизацию")
            this.isAuthing = true;
            
            if (config.USE_FREE_PROXIES) {
                const proxy = await FreeProxyService.getWorkingProxy();
                if (!proxy) {
                    throw new Error("Не удалось найти рабочий бесплатный прокси. Авторизация отменена.");
                }
                log.info(`Перезапускаю браузер с бесплатным прокси: ${proxy}`);
                await this.browser?.close().catch(e => log.error("Ошибка при закрытии старого браузера: " + e.message));
                this.browser = await puppeteer.launch({
                    headless: "new",
                    args: [
                        "--no-sandbox",
                        "--disable-local-file-access",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        `--proxy-server=http://${proxy}`
                    ],
                    ignoreHTTPSErrors: true,
                });
            }

            const {faculties_data, auth_cookie} = await ScheduleService.get_faculty_list(this.browser)
            console.log("Мы авторизованы")
            this.faculties_data = faculties_data
            this.auth_cookie = {cookie: auth_cookie, time: Date.now()}
            log.info("Произведена авторизация/получен список факультетов на schedule.buketov.edu.kz")
        } catch (e) {
            log.error("Не получилось авторизоваться на schedule.buketov.edu.kz | " + e.message)
        }finally {
            this.isAuthing = false;
        }
    }

    async authIfNot() {
        // Если уже идёт восстановление - выходим, не создаём лишних страниц
        if (this.isRecovering) {
            log.info("[Recovery Lock] Восстановление уже идёт, пропускаю authIfNot");
            return;
        }

        this.isRecovering = true;
        log.info("[Recovery Start] Начинаю проверку авторизации и восстановление");

        // Защита от зависания - если через 180 секунд флаг не сброшен, сбрасываем принудительно
        // (поиск бесплатного прокси может занять до 2 минут)
        if (this.recoveryTimeout) {
            clearTimeout(this.recoveryTimeout);
        }
        this.recoveryTimeout = setTimeout(() => {
            if (this.isRecovering) {
                log.error("[Recovery Timeout] Восстановление зависло больше 180 секунд, принудительно сбрасываю флаг!");
                this.isRecovering = false;
            }
        }, 180000);

        let page = null;
        try {
            page = await this.createOptimizedPage();
            const url = encodeURI(`${config.KSU_DOMAIN}/view1.php?id=5044&Kurs=3&Otdel=рус&Stud=10&d=1&m=Read`);
            await page.goto(url, {timeout: 10000})
            await page.waitForSelector("header", {timeout: 2000})

            const elementExists = await page.evaluate(() => {
                return !!document.querySelector('table');
            });

            if (!elementExists) {
                log.warn("[Recovery] Таблица не найдена, запускаю полную авторизацию");
                await this.auth()
            } else {
                log.info("[Recovery] Проверка прошла успешно, авторизация не требуется");
            }
        } catch (e) {
            log.error("[Recovery Error] Ошибка при попытке проверить авторизацию: " + e.message, {stack: e.stack})
            // Даже при ошибке пытаемся переавторизоваться
            try {
                log.warn("[Recovery Fallback] Запускаю авторизацию после ошибки проверки");
                await this.auth()
            } catch (authError) {
                log.error("[Recovery Fatal] Не удалось восстановить авторизацию: " + authError.message)
            }
        } finally {
            if (page) {
                await page.close().catch(e => log.error("Ошибка при закрытии страницы в authIfNot: " + e.message))
            }
            if (this.recoveryTimeout) {
                clearTimeout(this.recoveryTimeout);
                this.recoveryTimeout = null;
            }
            this.isRecovering = false;
            log.info("[Recovery End] Завершил попытку восстановления");
        }

    }

    // async isKsuAlive() {
    //     const page = await this.browser.newPage();
    //     try {
    //         await page.goto("https://schedule.buketov.edu.kz/view1.php?id=5044&Kurs=3&Otdel=рус&Stud=10&d=1&m=Read", {timeout:3000})
    //         return true; // Возвращает true, если сайт доступен, иначе false
    //     } catch (e) {
    //         log.error("Ошибка при попытке пингануть ксу: " + e.message, e)
    //         return false;
    //     }finally {
    //         await page.close()
    //     }
    // }

}

export default new BrowserController()