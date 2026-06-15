import config from "../config.js";
import puppeteer from "puppeteer";
import ScheduleService from "../services/ScheduleService.js";
import log from "../logging/logging.js";
import BrowserService from "../services/BrowserService.js";
import FreeProxyService from "../services/FreeProxyService.js";


class BrowserController {
    browser;
    auth_cookie;
    faculties_data;
    isAuthing;
    isRecovering;
    recoveryTimeout;
    isLaunching;
    launchTimeout;
    authTimeout; // Таймер для автосброса isAuthing
    _authPromise; // Единый промис авторизации для дедупликации

    constructor() {
        this.isRecovering = false;
        this.recoveryTimeout = null;
        this.isLaunching = false;
        this.launchTimeout = null;
        this.isAuthing = false;
        this.authTimeout = null;
        this._authPromise = null;
        if (config.START_BROWSER) {
            this.isAuthing = true;
            this.launchBrowser().then(() => log.info("Браузер запущен"))
        }
    }

    // Ожидание завершения авторизации (максимум waitMs миллисекунд)
    async _waitForAuth(waitMs = 30000) {
        const startTime = Date.now();
        const checkInterval = 500;
        while (this.isAuthing && (Date.now() - startTime) < waitMs) {
            await new Promise(resolve => setTimeout(resolve, checkInterval));
        }
        return !this.isAuthing;
    }

    allChecksCall = async (req, res, next) => {
        try {
            // Блокируем входящие запросы если идёт запуск браузера
            if (this.isLaunching) {
                // Ждём до 30 секунд пока браузер запустится
                for (let i = 0; i < 60; i++) {
                    await new Promise(resolve => setTimeout(resolve, 500));
                    if (!this.isLaunching) break;
                }
                if (this.isLaunching) {
                    res.set('Retry-After', '15');
                    return res.status(503).json({ error: "Идёт запуск браузера, попробуйте через 15 секунд" });
                }
            }

            if (!this.browser?.isConnected()) {
                await this.launchBrowser();
            }

            // Если идёт авторизация — ЖДЁМ до 30 секунд вместо мгновенного отказа
            if (this.isAuthing) {
                log.info(`[allChecksCall] Запрос ожидает завершения авторизации (до 30 сек)...`);
                const authCompleted = await this._waitForAuth(30000);
                if (!authCompleted) {
                    log.warn(`[allChecksCall] Авторизация не завершилась за 30 сек, отвечаю 503`);
                    res.set('Retry-After', '10');
                    return res.status(503).json({ error: "Идёт авторизация в КСУ, попробуйте через 10 секунд" });
                }
                log.info(`[allChecksCall] Авторизация завершилась, пропускаю запрос`);
            }

            // Если идёт восстановление — тоже ждём, но короче (15 сек)
            if (this.isRecovering) {
                log.info(`[allChecksCall] Запрос ожидает завершения восстановления (до 15 сек)...`);
                const startTime = Date.now();
                while (this.isRecovering && (Date.now() - startTime) < 15000) {
                    await new Promise(resolve => setTimeout(resolve, 500));
                }
                if (this.isRecovering) {
                    res.set('Retry-After', '10');
                    return res.status(503).json({ error: "Идёт восстановление соединения с КСУ, попробуйте через 10 секунд" });
                }
            }

            // Защита от утечки памяти - проверяем количество открытых страниц
            const pages = await this.browser.pages();
            const openPagesCount = pages.length;

            log.info(`[Memory Check] Открыто страниц: ${openPagesCount}`);

            // Если открыто более 5 страниц - закрываем все кроме первой и перезапускаем браузер
            if (openPagesCount > 5) {
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

                res.set('Retry-After', '5');
                return res.status(503).json({ error: "Браузер был перезапущен из-за утечки памяти. Попробуйте запрос снова." });
            }

            // Дополнительная проверка - если открыто 3-5 страниц, закрываем лишние
            if (openPagesCount > 3) {
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
                const memoryFlags = [
                    "--no-sandbox",
                    "--disable-local-file-access",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--single-process",
                    "--no-zygote",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-translate",
                    "--no-first-run",
                    "--js-flags=--max-old-space-size=128"
                ];
                if (config.PROXY_LOGIN && config.USE_PROXY) {
                    this.browser = await puppeteer.launch({
                        headless: "new",
                        args: [...memoryFlags, `--proxy-server=${config.HTTP_PROXY}`],
                        ignoreHTTPSErrors: true,
                    })
                } else {
                    this.browser = await puppeteer.launch({
                        headless: "new",
                        args: memoryFlags,
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
        // Дедупликация: если auth() уже выполняется — возвращаем тот же промис
        if (this._authPromise) {
            log.info("[Auth Dedup] auth() уже выполняется, жду результат...");
            return this._authPromise;
        }

        this._authPromise = this._doAuth();
        try {
            return await this._authPromise;
        } finally {
            this._authPromise = null;
        }
    }

    async _doAuth() {
        console.log("Начинаю авторизацию");
        this.isAuthing = true;
        let success = false;
        let attempts = 0;
        const maxAttempts = 15;

        // Аварийный таймаут: если auth зависнет больше 120 сек — принудительно сбросить флаг
        if (this.authTimeout) {
            clearTimeout(this.authTimeout);
        }
        this.authTimeout = setTimeout(() => {
            if (this.isAuthing) {
                log.error("[Auth Timeout] Авторизация зависла больше 120 секунд, принудительно сбрасываю isAuthing!");
                this.isAuthing = false;
            }
        }, 120000);

        try {
            while (!success && attempts < maxAttempts) {
                attempts++;
                try {
                    if (config.USE_FREE_PROXIES) {
                        const proxy = await FreeProxyService.getWorkingProxy();
                        if (!proxy) {
                            throw new Error("Не удалось найти рабочий бесплатный прокси. Авторизация отменена.");
                        }
                        log.info(`[Auth Attempt ${attempts}/${maxAttempts}] Запускаю браузер с прокси: ${proxy}`);
                        await this.browser?.close().catch(e => log.error("Ошибка при закрытии старого браузера: " + e.message));
                        await new Promise(resolve => setTimeout(resolve, 2000));
                        global.gc?.();
                        this.browser = await puppeteer.launch({
                            headless: "new",
                            args: [
                                "--no-sandbox",
                                "--disable-local-file-access",
                                "--disable-setuid-sandbox",
                                "--disable-dev-shm-usage",
                                "--single-process",
                                "--no-zygote",
                                "--disable-gpu",
                                "--disable-software-rasterizer",
                                "--disable-extensions",
                                "--disable-background-networking",
                                "--no-first-run",
                                "--js-flags=--max-old-space-size=128",
                                `--proxy-server=http://${proxy}`
                            ],
                            ignoreHTTPSErrors: true,
                        });
                    }

                    const {faculties_data, auth_cookie} = await ScheduleService.get_faculty_list(this.browser);
                    console.log("Мы авторизованы");
                    this.faculties_data = faculties_data;
                    this.auth_cookie = {cookie: auth_cookie, time: Date.now()};
                    log.info("Произведена авторизация/получен список факультетов на schedule.buketov.edu.kz");
                    success = true;
                } catch (innerError) {
                    log.error(`[Auth Attempt ${attempts}/${maxAttempts}] Ошибка: ` + innerError.message);
                    if (attempts >= maxAttempts) {
                        throw new Error("Исчерпан лимит попыток авторизации.");
                    }
                }
            }
        } catch (e) {
            log.error("Не получилось авторизоваться на schedule.buketov.edu.kz | " + e.message);
        } finally {
            if (this.authTimeout) {
                clearTimeout(this.authTimeout);
                this.authTimeout = null;
            }
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

}

export default new BrowserController()