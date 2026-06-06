import config from "../config.js";
import ScheduleService from "../services/ScheduleService.js";
import log from "../logging/logging.js";
import FreeProxyService from "../services/FreeProxyService.js";
import axios from "axios";
import { HttpProxyAgent } from "http-proxy-agent";
import { HttpsProxyAgent } from "https-proxy-agent";
import https from "https";
import * as cheerio from "cheerio";

class BrowserController {
    axiosClient;
    auth_cookie; // string: "PHPSESSID=..."
    faculties_data;
    isAuthing;
    isRecovering; 
    recoveryTimeout;
    isLaunching; 
    launchTimeout;

    constructor() {
        this.isRecovering = false;
        this.recoveryTimeout = null;
        this.isLaunching = false;
        this.isAuthing = false;

        this.initAxiosClient();

        if (config.START_BROWSER) {
            this.isAuthing = true;
            if (config.USE_FREE_PROXIES) {
                FreeProxyService.initPool().then(() => {
                    this.auth().then(() => log.info("HTTP клиент запущен и авторизован"))
                });
            } else {
                this.auth().then(() => log.info("HTTP клиент запущен и авторизован"))
            }
        }
    }

    initAxiosClient(proxyString = null) {
        const agentOptions = { rejectUnauthorized: false };
        let httpAgent = null;
        let httpsAgent = new https.Agent(agentOptions);

        if (proxyString) {
            const proxyUrl = `http://${proxyString}`;
            httpAgent = new HttpProxyAgent(proxyUrl);
            httpsAgent = new HttpsProxyAgent(proxyUrl, { rejectUnauthorized: false });
        } else if (config.USE_PROXY && config.PROXY_LOGIN) {
            const proxyUrl = `http://${config.PROXY_LOGIN}:${config.PROXY_PASSWORD}@${config.HTTP_PROXY}`;
            httpAgent = new HttpProxyAgent(proxyUrl);
            httpsAgent = new HttpsProxyAgent(proxyUrl, { rejectUnauthorized: false });
        }

        const KSU_IP_URL = config.KSU_DOMAIN.replace('schedule.buketov.edu.kz', '188.0.155.151');

        this.axiosClient = axios.create({
            baseURL: KSU_IP_URL,
            httpAgent,
            httpsAgent,
            timeout: 10000,
            headers: {
                'Host': 'schedule.buketov.edu.kz',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
            maxRedirects: 5,
            validateStatus: status => status >= 200 && status < 500
        });
    }

    allChecksCall = async (req, res, next) => {
        try {
            if (this.isAuthing) {
                return res.status(503).set('Retry-After', '10').json({
                    error: "Идёт авторизация в КарГУ, попробуйте через несколько секунд"
                });
            }

            if (this.isRecovering) {
                return res.status(503).set('Retry-After', '10').json({
                    error: "Идёт восстановление соединения с КарГУ, попробуйте через несколько секунд"
                });
            }

            next();
        } catch (e) {
            log.error("Ошибка в allChecksCall мидлваре: " + e.message, e)
            next(e)
        }
    }
    async launchBrowser() {
        await this.auth();
    }

    async restartBrowser(req, res, next) {
        try {
            await this.auth();
            return res.json("Restarted (HTTP)")
        } catch (e) {
            next(e)
        }
    }

    async makeHtmlScreenShot(req, res, next) {
        return res.status(404).send("Screenshots disabled in pure HTTP mode");
    }

    async auth() {
        try {
            log.info("Начинаю HTTP авторизацию");
            this.isAuthing = true;
            
            if (config.USE_FREE_PROXIES) {
                const maxProxyRetries = 3;
                let lastError = null;

                for (let attempt = 1; attempt <= maxProxyRetries; attempt++) {
                    const proxy = await FreeProxyService.getWorkingProxy();
                    if (!proxy) {
                        throw new Error("Не удалось найти рабочий бесплатный прокси. Авторизация отменена.");
                    }
                    
                    log.info(`[Proxy Auth] Попытка ${attempt}/${maxProxyRetries} с прокси: ${proxy}`);
                    
                    try {
                        this.initAxiosClient(proxy);
                        const {faculties_data, auth_cookie} = await ScheduleService.get_faculty_list(this.axiosClient);
                        this.faculties_data = faculties_data;
                        this.auth_cookie = {cookie: auth_cookie, time: Date.now()};
                        this.axiosClient.defaults.headers.common['Cookie'] = auth_cookie;
                        
                        log.info("HTTP Авторизация успешна через прокси " + proxy);
                        return; // Успех!
                    } catch (proxyErr) {
                        lastError = proxyErr;
                        log.warn(`[Proxy Auth] Прокси ${proxy} не сработал (попытка ${attempt}): ${proxyErr.message}`);
                    }
                }
                throw lastError || new Error("Все прокси-попытки провалились");
            }
            this.initAxiosClient(null);
            const {faculties_data, auth_cookie} = await ScheduleService.get_faculty_list(this.axiosClient);
            this.faculties_data = faculties_data;
            this.auth_cookie = {cookie: auth_cookie, time: Date.now()};
            this.axiosClient.defaults.headers.common['Cookie'] = auth_cookie;
            log.info("HTTP Авторизация успешна (без прокси)");
        } catch (e) {
            log.error("Не получилось авторизоваться на schedule.buketov.edu.kz | " + e.message);
        } finally {
            this.isAuthing = false;
        }
    }

    async authIfNot() {
        if (this.isRecovering) {
            log.info("[Recovery Lock] Восстановление уже идёт, пропускаю authIfNot");
            return;
        }

        this.isRecovering = true;
        log.info("[Recovery Start] Начинаю проверку авторизации и восстановление");

        if (this.recoveryTimeout) {
            clearTimeout(this.recoveryTimeout);
        }
        this.recoveryTimeout = setTimeout(() => {
            if (this.isRecovering) {
                log.error("[Recovery Timeout] Восстановление зависло больше 180 секунд!");
                this.isRecovering = false;
            }
        }, 180000);

        try {
            const res = await this.axiosClient.get('/view1.php?id=5044&Kurs=3&Otdel=%D1%80%D1%83%D1%81&Stud=10&d=1&m=Read', {timeout: 10000});
            const $ = cheerio.load(res.data);
            const elementExists = $('table').length > 0;

            if (!elementExists || $('h1').text().includes('Forbidden')) {
                log.warn("[Recovery] Таблица не найдена или Forbidden, запускаю полную авторизацию");
                await this.auth();
            } else {
                log.info("[Recovery] Проверка прошла успешно, авторизация не требуется");
            }
        } catch (e) {
            log.error("[Recovery Error] Ошибка при попытке проверить авторизацию: " + e.message);
            try {
                log.warn("[Recovery Fallback] Запускаю авторизацию после ошибки проверки");
                await this.auth();
            } catch (authError) {
                log.error("[Recovery Fatal] Не удалось восстановить авторизацию: " + authError.message);
            }
        } finally {
            if (this.recoveryTimeout) {
                clearTimeout(this.recoveryTimeout);
                this.recoveryTimeout = null;
            }
            this.isRecovering = false;
            log.info("[Recovery End] Завершил попытку восстановления");
        }
    }
}

export default new BrowserController();