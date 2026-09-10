import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import log from "../logging/logging.js";
import config from "../config.js";
import FreeProxyService from "./FreeProxyService.js";

const COOKIE_TTL = 30 * 60 * 1000; // 30 ╨╝╨╕╨╜╤Г╤В
const AUTH_TIMEOUT = 10000; // 10 ╤Б╨╡╨║ ╨╜╨░ ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤О
const VERIFY_TIMEOUT = 8000; // 8 ╤Б╨╡╨║ ╨╜╨░ ╨┐╤А╨╛╨▓╨╡╤А╨║╤Г ╨║╤Г╨║╨╕

class KsuAuthService {
    constructor() {
        this._cookie = null;
        this._cookieExpiry = 0;
        this._authPromise = null; // ╨┤╨╡╨┤╤Г╨┐╨╗╨╕╨║╨░╤Ж╨╕╤П
    }

    /**
     * ╨Я╨╛╨╗╤Г╤З╨╕╤В╤М ╨▓╨░╨╗╨╕╨┤╨╜╤Г╤О ╨║╤Г╨║╤Г PHPSESSID.
     * ╨Ъ╤Н╤И╨╕╤А╤Г╨╡╤В ╨║╤Г╨║╤Г ╨╜╨░ 30 ╨╝╨╕╨╜╤Г╤В. ╨Я╤А╨╕ ╨║╨╛╨╜╨║╤Г╤А╨╡╨╜╤В╨╜╤Л╤Е ╨▓╤Л╨╖╨╛╨▓╨░╤Е тАФ ╨╛╨┤╨╕╨╜ ╨┐╤А╨╛╨╝╨╕╤Б.
     */
    async getCookie() {
        if (this._cookie && Date.now() < this._cookieExpiry) {
            return this._cookie;
        }
        // ╨Ф╨╡╨┤╤Г╨┐╨╗╨╕╨║╨░╤Ж╨╕╤П: ╨╡╤Б╨╗╨╕ auth ╤Г╨╢╨╡ ╨╕╨┤╤С╤В тАФ ╨╢╨┤╤С╨╝ ╤В╨╛╤В ╨╢╨╡ ╨┐╤А╨╛╨╝╨╕╤Б
        if (this._authPromise) {
            log.info("[KsuAuth] ╨г╨╢╨╡ ╨╕╨┤╤С╤В ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤П, ╨╢╨┤╤Г...");
            return this._authPromise;
        }
        this._authPromise = this._authorize();
        try {
            return await this._authPromise;
        } finally {
            this._authPromise = null;
        }
    }

    /**
     * ╨б╨▒╤А╨╛╤Б╨╕╤В╤М ╨║╤Н╤И ╨║╤Г╨║╨╕ (╨▓╤Л╨╖╤Л╨▓╨░╨╡╤В╤Б╤П ╨┐╤А╨╕ 302 ╨╕╨╗╨╕ ╨╛╤И╨╕╨▒╨║╨╡ ╤Б╨╡╤Б╤Б╨╕╨╕)
     */
    invalidate() {
        log.info("[KsuAuth] ╨Ъ╤Н╤И ╨║╤Г╨║╨╕ ╤Б╨▒╤А╨╛╤И╨╡╨╜");
        this._cookie = null;
        this._cookieExpiry = 0;
    }

    /**
     * ╨Р╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤П ╨╜╨░ ╨Ъ╨░╤А╨У╨г ╤З╨╡╤А╨╡╨╖ axios POST.
     * ╨б╤В╤А╨░╤В╨╡╨│╨╕╤П: ╤Б╨╜╨░╤З╨░╨╗╨░ ╨╜╨░╨┐╤А╤П╨╝╤Г╤О, ╨┐╨╛╤В╨╛╨╝ ╤З╨╡╤А╨╡╨╖ ╨┐╤А╨╛╨║╤Б╨╕.
     */
    async _authorize() {
        // Попытки 1-3: Прямое подключение с паузой 2 секунды
        for (let attempt = 1; attempt <= 3; attempt++) {
            try {
                log.info(`[KsuAuth] Попытка прямой авторизации ${attempt}/3...`);
                const cookie = await this._tryAuth(null);
                if (cookie) {
                    log.info("[KsuAuth] Авторизация без прокси успешна!");
                    this._cookie = cookie;
                    this._cookieExpiry = Date.now() + COOKIE_TTL;
                    return cookie;
                }
            } catch (e) {
                log.warn(`[KsuAuth] Попытка ${attempt}/3 не удалась: ${e.message}`);
                if (attempt < 3) {
                    await new Promise(r => setTimeout(r, 2000));
                }
            }
        }

        // ╨Я╨╛╨┐╤Л╤В╨║╨╕ 2-6: ╤З╨╡╤А╨╡╨╖ ╨┐╤А╨╛╨║╤Б╨╕ ╨╕╨╖ ╨┐╤Г╨╗╨░
        const maxAttempts = 5;
        let lastError = null;
        for (let i = 0; i < maxAttempts; i++) {
            const proxy = await FreeProxyService.getNextProxy();
            if (!proxy) {
                log.warn(`[KsuAuth] ╨Я╨╛╨┐╤Л╤В╨║╨░ ${i + 1}/${maxAttempts}: ╨╜╨╡╤В ╨┐╤А╨╛╨║╤Б╨╕ ╨▓ ╨┐╤Г╨╗╨╡`);
                await new Promise(r => setTimeout(r, 1000));
                continue;
            }
            try {
                log.info(`[KsuAuth] ╨Я╨╛╨┐╤Л╤В╨║╨░ ${i + 1}/${maxAttempts} ╤З╨╡╤А╨╡╨╖ ╨┐╤А╨╛╨║╤Б╨╕: ${proxy}`);
                const cookie = await this._tryAuth(proxy);
                if (cookie) {
                    log.info(`[KsuAuth] тЬЕ ╨Р╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤П ╤З╨╡╤А╨╡╨╖ ╨┐╤А╨╛╨║╤Б╨╕ ${proxy} ╤Г╤Б╨┐╨╡╤И╨╜╨░!`);
                    this._cookie = cookie;
                    this._cookieExpiry = Date.now() + COOKIE_TTL;
                    return cookie;
                }
            } catch (e) {
                lastError = e;
                log.warn(`[KsuAuth] ╨Я╨╛╨┐╤Л╤В╨║╨░ ${i + 1}/${maxAttempts} ╨╜╨╡ ╤Г╨┤╨░╨╗╨░╤Б╤М: ${e.message}`);
                FreeProxyService.markProxyDead(proxy);
            }
        }

        log.error("[KsuAuth] тЭМ ╨Т╤Б╨╡ ╨┐╨╛╨┐╤Л╤В╨║╨╕ ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╨╕ ╨╕╤Б╤З╨╡╤А╨┐╨░╨╜╤Л");
        throw lastError || new Error("╨Э╨╡ ╤Г╨┤╨░╨╗╨╛╤Б╤М ╨░╨▓╤В╨╛╤А╨╕╨╖╨╛╨▓╨░╤В╤М╤Б╤П ╨╜╨░ ╨Ъ╨░╤А╨У╨г");
    }

    /**
     * ╨Ю╨┤╨╜╨░ ╨┐╨╛╨┐╤Л╤В╨║╨░ ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╨╕.
     * @param {string|null} proxy - ╨┐╤А╨╛╨║╤Б╨╕ (null = ╨┐╤А╤П╨╝╨╛╨╡ ╨┐╨╛╨┤╨║╨╗╤О╤З╨╡╨╜╨╕╨╡)
     * @returns {string|null} - ╨║╤Г╨║╨░ "PHPSESSID=xxx" ╨╕╨╗╨╕ null
     */
    _checkCloudflare(response, stepName) {
        if (!response) return;
        const headers = response.headers || {};
        const server = (headers.server || '').toLowerCase();
        const body = typeof response.data === 'string' ? response.data : '';
        const lowerBody = body.toLowerCase();
        
        const hasCloudflareTitle = lowerBody.includes('<title>just a moment...') || lowerBody.includes('<title>attention required!');
        const hasChlOpt = lowerBody.includes('__cf_chl_opt') || lowerBody.includes('cf-challenge');
        
        if (response.status === 403 || response.status === 503 || server === 'cloudflare' || hasCloudflareTitle || hasChlOpt) {
            // ╨г╨▒╨╡╨┤╨╕╨╝╤Б╤П, ╤З╤В╨╛ ╤Н╤В╨╛ ╨╜╨╡ ╨╛╨▒╤Л╤З╨╜╤Л╨╣ 403/503 ╨╛╤В nginx, ╨░ ╨╕╨╝╨╡╨╜╨╜╨╛ Cloudflare (╨╕╨╗╨╕ ╤П╨▓╨╜╤Л╨╡ ╨┐╤А╨╕╨╖╨╜╨░╨║╨╕)
            const isCf = server === 'cloudflare' || hasCloudflareTitle || hasChlOpt || lowerBody.includes('cloudflare-captcha') || (response.status === 403 && lowerBody.includes('cf-ray'));
            if (isCf) {
                throw new Error(`╨С╨╗╨╛╨║╨╕╤А╨╛╨▓╨║╨░ Cloudflare ╨╜╨░ ${stepName}`);
            }
        }
    }

    /**
     * ╨Ю╨┤╨╜╨░ ╨┐╨╛╨┐╤Л╤В╨║╨░ ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╨╕.
     * @param {string|null} proxy - ╨┐╤А╨╛╨║╤Б╨╕ (null = ╨┐╤А╤П╨╝╨╛╨╡ ╨┐╨╛╨┤╨║╨╗╤О╤З╨╡╨╜╨╕╨╡)
     * @returns {string|null} - ╨║╤Г╨║╨░ "PHPSESSID=xxx" ╨╕╨╗╨╕ null
     */
    async _tryAuth(proxy) {
        const domain = config.KSU_DOMAIN;
        const agentOpts = proxy ? { rejectUnauthorized: false } : undefined;
        const httpsAgent = proxy ? new HttpsProxyAgent(`http://${proxy}`, agentOpts) : undefined;
        const httpAgent = proxy ? new HttpProxyAgent(`http://${proxy}`) : undefined;

        const baseHeaders = {
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
        };

        log.info(`[KsuAuth _tryAuth] ╨и╨░╨│ 1: GET login.php`);
        // ╨и╨░╨│ 1: GET login.php тАФ ╨┐╨╛╨╗╤Г╤З╨╕╤В╤М ╨╜╨░╤З╨░╨╗╤М╨╜╤Г╤О ╤Б╨╡╤Б╤Б╨╕╤О
        const loginPageRes = await axios.get(`${domain}/login.php`, {
            httpsAgent,
            httpAgent,
            proxy: false,
            timeout: AUTH_TIMEOUT,
            maxRedirects: 0,
            validateStatus: s => s < 400,
            headers: baseHeaders
        });

        this._checkCloudflare(loginPageRes, "╨и╨░╨│ 1 (GET login.php)");

        // ╨Ш╨╖╨▓╨╗╨╡╨║╨░╨╡╨╝ PHPSESSID ╨╕╨╖ set-cookie ╨╖╨░╨│╨╛╨╗╨╛╨▓╨║╨░
        let sessionCookie = this._extractSessionCookie(loginPageRes.headers['set-cookie']);
        if (!sessionCookie) {
            throw new Error("╨б╨╡╤А╨▓╨╡╤А ╨╜╨╡ ╨▓╨╡╤А╨╜╤Г╨╗ PHPSESSID ╨╜╨░ ╤И╨░╨│╨╡ 1");
        }
        log.info(`[KsuAuth _tryAuth] Шаг 1 успешен, cookie: ${sessionCookie}`);

        await new Promise(r => setTimeout(r, 800));

        log.info(`[KsuAuth _tryAuth] Шаг 2: POST login.php`);
        // Шаг 2: POST login.php — отправить логин/пароль
        const loginRes = await axios.post(`${domain}/login.php`, 
            `login=${encodeURIComponent(config.KSU_LOGIN)}&password=${encodeURIComponent(config.KSU_PASSWORD)}&submit=${encodeURIComponent('Вход')}`,
            {
                httpsAgent,
                httpAgent,
                proxy: false,
                timeout: AUTH_TIMEOUT,
                maxRedirects: 0,
                validateStatus: s => s < 400 || s === 302,
                headers: {
                    ...baseHeaders,
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Cookie': sessionCookie,
                    'Origin': domain,
                    'Referer': `${domain}/login.php`
                }
            }
        );

        this._checkCloudflare(loginRes, "Шаг 2 (POST login.php)");

        // Если прилетел новый Set-Cookie, обновляем
        const newCookie = this._extractSessionCookie(loginRes.headers['set-cookie']);
        if (newCookie) {
            sessionCookie = newCookie;
            log.info(`[KsuAuth _tryAuth] Шаг 2 выдал новый cookie: ${sessionCookie}`);
        }

        await new Promise(r => setTimeout(r, 800));

        log.info(`[KsuAuth _tryAuth] Шаг 3: GET главная страница /`);
        // Шаг 3: GET главная страница — проверить что мы зашли
        const mainRes = await axios.get(`${domain}/`, {
            httpsAgent,
            httpAgent,
            proxy: false,
            timeout: AUTH_TIMEOUT,
            maxRedirects: 0,
            validateStatus: s => s < 400 || s === 302,
            headers: {
                ...baseHeaders,
                'Cookie': sessionCookie,
                'Referer': `${domain}/login.php`
            }
        });

        this._checkCloudflare(mainRes, "Шаг 3 (GET /)");

        // Если нас редиректит на login.php — значит логин не сработал!
        if (mainRes.status === 302 && mainRes.headers.location && mainRes.headers.location.includes('login.php')) {
            throw new Error("Неверный логин или пароль (редирект на login.php на шаге 3)");
        }

        // Если редиректит куда-то еще (например, на index.php), делаем туда запрос
        let mainBody = mainRes.data;
        if (mainRes.status === 302 && mainRes.headers.location) {
            const redirectUrl = mainRes.headers.location.startsWith('http') 
                ? mainRes.headers.location 
                : `${domain}/${mainRes.headers.location.replace(/^\//, '')}`;
            log.info(`[KsuAuth _tryAuth] Перехожу по редиректу: ${redirectUrl}`);
            await new Promise(r => setTimeout(r, 800));
            const redirectRes = await axios.get(redirectUrl, {
                httpsAgent,
                httpAgent,
                proxy: false,
                timeout: AUTH_TIMEOUT,
                maxRedirects: 0,
                validateStatus: s => s < 400,
                headers: {
                    ...baseHeaders,
                    'Cookie': sessionCookie,
                    'Referer': `${domain}/`
                }
            });
            this._checkCloudflare(redirectRes, "Шаг 3 Редирект");
            mainBody = redirectRes.data;
        }

        // Извлечь список факультетов для POST
        const bodyText = typeof mainBody === 'string' ? mainBody : '';
        const selectMatch = bodyText.match(/<option[^>]*>([^<]+)<\/option>/i);
        const firstFaculty = selectMatch ? selectMatch[1] : null;

        if (firstFaculty) {
            log.info(`[KsuAuth _tryAuth] Выбираем факультет: ${firstFaculty}`);
            await new Promise(r => setTimeout(r, 800));
            // POST выбор факультета — завершает авторизацию
            const selectRes = await axios.post(`${domain}/index.php?x`,
                `Login=${encodeURIComponent(firstFaculty)}&pw=`,
                {
                    httpsAgent,
                    httpAgent,
                    proxy: false,
                    timeout: AUTH_TIMEOUT,
                    maxRedirects: 0,
                    validateStatus: s => s < 500,
                    headers: {
                        ...baseHeaders,
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Cookie': sessionCookie,
                        'Origin': domain,
                        'Referer': `${domain}/`
                    }
                }
            );

            this._checkCloudflare(selectRes, "Выбор факультета (POST /)");

            const newerCookie = this._extractSessionCookie(selectRes.headers['set-cookie']);
            if (newerCookie) sessionCookie = newerCookie;

            // Делаем GET запрос на stud.php, чтобы завершить сессию входа (имитируем редирект браузера)
            log.info(`[KsuAuth _tryAuth] Переход на stud.php для завершения сессии`);
            await new Promise(r => setTimeout(r, 800));
            const studRes = await axios.get(`${domain}/stud.php`, {
                httpsAgent,
                httpAgent,
                proxy: false,
                timeout: AUTH_TIMEOUT,
                maxRedirects: 0,
                validateStatus: s => s < 400 || s === 302,
                headers: {
                    ...baseHeaders,
                    'Cookie': sessionCookie,
                    'Referer': `${domain}/index.php?x`
                }
            });
            this._checkCloudflare(studRes, "Шаг 3.5 (GET stud.php)");
            
            const newestCookie = this._extractSessionCookie(studRes.headers['set-cookie']);
            if (newestCookie) sessionCookie = newestCookie;

            const studBody = (typeof studRes.data === 'string' ? studRes.data : '').toLowerCase();
            if (studRes.status === 200 && (
                studBody.includes('мамандық') ||
                studBody.includes('специальн') ||
                studBody.includes('grupps') ||
                studBody.includes('<select') ||
                studBody.includes('<table')
            )) {
                log.info("[KsuAuth _tryAuth] Сессия успешно подтверждена на шаге 3.5");
                return sessionCookie;
            }
        } else {
            log.warn(`[KsuAuth _tryAuth] Список факультетов не найден на главной странице! Статус ответа: ${mainRes.status}.`);
        }

        // Шаг 4: Верификация сессии
        log.info(`[KsuAuth _tryAuth] Шаг 4: Верификация сессии`);
        await new Promise(r => setTimeout(r, 800));
        const verified = await this._verifyCookie(sessionCookie, proxy);
        if (!verified) {
            throw new Error("Кука не прошла верификацию — расписание недоступно");
        }

        return sessionCookie;
    }

    /**
     * ╨Я╤А╨╛╨▓╨╡╤А╨╕╤В╤М ╤З╤В╨╛ ╨║╤Г╨║╨░ ╨┤╨░╤С╤В ╨┤╨╛╤Б╤В╤Г╨┐ ╨║ ╤А╨░╤Б╨┐╨╕╤Б╨░╨╜╨╕╤О (GET view1.php)
     */
    async _verifyCookie(cookie, proxy) {
        try {
            const domain = config.KSU_DOMAIN;
            const httpsAgent = proxy ? new HttpsProxyAgent(`http://${proxy}`, { rejectUnauthorized: false }) : undefined;
            const httpAgent = proxy ? new HttpProxyAgent(`http://${proxy}`) : undefined;

            const url = `${domain}/stud.php`;
            const res = await axios.get(url, {
                httpsAgent,
                httpAgent,
                proxy: false,
                timeout: VERIFY_TIMEOUT,
                maxRedirects: 0,
                validateStatus: () => true,
                headers: {
                    'upgrade-insecure-requests': '1',
                    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Cookie': cookie,
                    'Referer': `${domain}/`
                }
            });

            if (res.status === 302 || res.status === 301 || res.status === 403) {
                log.warn(`[KsuAuth Verify] Cookie invalid. Status: ${res.status}, Location: ${res.headers.location}`);
                return false;
            }

            this._checkCloudflare(res, "Verify GET");

            const body = typeof res.data === 'string' ? res.data : '';
            const lowerBody = body.toLowerCase();
            
            if (res.status === 200 && (
                lowerBody.includes('мамандық') ||
                lowerBody.includes('специальн') ||
                lowerBody.includes('grupps') ||
                lowerBody.includes('<select') ||
                lowerBody.includes('<table') ||
                lowerBody.includes('name="fak"')
            )) {
                return true;
            }

            log.warn(`[KsuAuth Verify] Page does not contain schedule or faculty form. Status: ${res.status}. Length: ${body.length}.`);
            return false;
        } catch (e) {
            log.warn(`[KsuAuth Verify] Verification error: ${e.message}`);
            return false;
        }
    }

    /**
     * ╨Ш╨╖╨▓╨╗╨╡╤З╤М PHPSESSID ╨╕╨╖ ╨╝╨░╤Б╤Б╨╕╨▓╨░ set-cookie ╨╖╨░╨│╨╛╨╗╨╛╨▓╨║╨╛╨▓
     */
    _extractSessionCookie(setCookieHeaders) {
        if (!setCookieHeaders) return null;
        const cookies = Array.isArray(setCookieHeaders) ? setCookieHeaders : [setCookieHeaders];
        for (const c of cookies) {
            const match = c.match(/PHPSESSID=([^;]+)/);
            if (match) {
                return `PHPSESSID=${match[1]}`;
            }
        }
        return null;
    }
}

export default new KsuAuthService();
