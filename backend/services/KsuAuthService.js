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
        log.info("[KsuAuth] ╨Э╨░╤З╨╕╨╜╨░╤О ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤О...");

        // ╨Я╨╛╨┐╤Л╤В╨║╨░ 1: ╨╜╨░╨┐╤А╤П╨╝╤Г╤О (╨▒╨╡╨╖ ╨┐╤А╨╛╨║╤Б╨╕) тАФ ╨╡╤Б╨╗╨╕ DNS ╨╜╨░ Render ╤А╨░╨▒╨╛╤В╨░╨╡╤В, ╤Н╤В╨╛ ╤Б╨░╨╝╤Л╨╣ ╨▒╤Л╤Б╤В╤А╤Л╨╣ ╨┐╤Г╤В╤М
        try {
            const cookie = await this._tryAuth(null);
            if (cookie) {
                log.info("[KsuAuth] тЬЕ ╨Р╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤П ╨▒╨╡╨╖ ╨┐╤А╨╛╨║╤Б╨╕ ╤Г╤Б╨┐╨╡╤И╨╜╨░!");
                this._cookie = cookie;
                this._cookieExpiry = Date.now() + COOKIE_TTL;
                return cookie;
            }
        } catch (e) {
            log.warn(`[KsuAuth] ╨Я╤А╤П╨╝╨░╤П ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤П ╨╜╨╡ ╤Г╨┤╨░╨╗╨░╤Б╤М: ${e.message}`);
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
        log.info(`[KsuAuth _tryAuth] ╨и╨░╨│ 1 ╤Г╤Б╨┐╨╡╤И╨╡╨╜, cookie: ${sessionCookie}`);

        log.info(`[KsuAuth _tryAuth] ╨и╨░╨│ 2: POST login.php`);
        // ╨и╨░╨│ 2: POST login.php тАФ ╨╛╤В╨┐╤А╨░╨▓╨╕╤В╤М ╨╗╨╛╨│╨╕╨╜/╨┐╨░╤А╨╛╨╗╤М
        const loginRes = await axios.post(`${domain}/login.php`, 
            `login=${encodeURIComponent(config.KSU_LOGIN)}&password=${encodeURIComponent(config.KSU_PASSWORD)}`,
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

        this._checkCloudflare(loginRes, "╨и╨░╨│ 2 (POST login.php)");

        // ╨Х╤Б╨╗╨╕ ╨┐╤А╨╕╨╗╨╡╤В╨╡╨╗ ╨╜╨╛╨▓╤Л╨╣ Set-Cookie, ╨╛╨▒╨╜╨╛╨▓╨╗╤П╨╡╨╝
        const newCookie = this._extractSessionCookie(loginRes.headers['set-cookie']);
        if (newCookie) {
            sessionCookie = newCookie;
            log.info(`[KsuAuth _tryAuth] ╨и╨░╨│ 2 ╨▓╤Л╨┤╨░╨╗ ╨╜╨╛╨▓╤Л╨╣ cookie: ${sessionCookie}`);
        }

        log.info(`[KsuAuth _tryAuth] ╨и╨░╨│ 3: GET ╨│╨╗╨░╨▓╨╜╨░╤П ╤Б╤В╤А╨░╨╜╨╕╤Ж╨░ /`);
        // ╨и╨░╨│ 3: GET ╨│╨╗╨░╨▓╨╜╨░╤П ╤Б╤В╤А╨░╨╜╨╕╤Ж╨░ тАФ ╨┐╤А╨╛╨▓╨╡╤А╨╕╤В╤М ╤З╤В╨╛ ╨╝╤Л ╨╖╨░╤И╨╗╨╕
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

        this._checkCloudflare(mainRes, "╨и╨░╨│ 3 (GET /)");

        // ╨Х╤Б╨╗╨╕ ╨╜╨░╤Б ╤А╨╡╨┤╨╕╤А╨╡╨║╤В╨╕╤В ╨╜╨░ login.php тАФ ╨╖╨╜╨░╤З╨╕╤В ╨╗╨╛╨│╨╕╨╜ ╨╜╨╡ ╤Б╤А╨░╨▒╨╛╤В╨░╨╗!
        if (mainRes.status === 302 && mainRes.headers.location && mainRes.headers.location.includes('login.php')) {
            throw new Error("╨Э╨╡╨▓╨╡╤А╨╜╤Л╨╣ ╨╗╨╛╨│╨╕╨╜ ╨╕╨╗╨╕ ╨┐╨░╤А╨╛╨╗╤М (╤А╨╡╨┤╨╕╤А╨╡╨║╤В ╨╜╨░ login.php ╨╜╨░ ╤И╨░╨│╨╡ 3)");
        }

        // ╨Х╤Б╨╗╨╕ ╤А╨╡╨┤╨╕╤А╨╡╨║╤В╨╕╤В ╨║╤Г╨┤╨░-╤В╨╛ ╨╡╤Й╨╡ (╨╜╨░╨┐╤А╨╕╨╝╨╡╤А, ╨╜╨░ index.php), ╨┤╨╡╨╗╨░╨╡╨╝ ╤В╤Г╨┤╨░ ╨╖╨░╨┐╤А╨╛╤Б
        let mainBody = mainRes.data;
        if (mainRes.status === 302 && mainRes.headers.location) {
            const redirectUrl = mainRes.headers.location.startsWith('http') 
                ? mainRes.headers.location 
                : `${domain}/${mainRes.headers.location.replace(/^\//, '')}`;
            log.info(`[KsuAuth _tryAuth] ╨Я╨╡╤А╨╡╤Е╨╛╨╢╤Г ╨┐╨╛ ╤А╨╡╨┤╨╕╤А╨╡╨║╤В╤Г: ${redirectUrl}`);
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
            this._checkCloudflare(redirectRes, "╨и╨░╨│ 3 ╨а╨╡╨┤╨╕╤А╨╡╨║╤В");
            mainBody = redirectRes.data;
        }

        // ╨Ш╨╖╨▓╨╗╨╡╤З╤М ╤Б╨┐╨╕╤Б╨╛╨║ ╤Д╨░╨║╤Г╨╗╤М╤В╨╡╤В╨╛╨▓ ╨┤╨╗╤П POST
        const bodyText = typeof mainBody === 'string' ? mainBody : '';
        const selectMatch = bodyText.match(/<option[^>]*>([^<]+)<\/option>/i);
        const firstFaculty = selectMatch ? selectMatch[1] : null;

        if (firstFaculty) {
            log.info(`[KsuAuth _tryAuth] ╨Т╤Л╨▒╨╕╤А╨░╨╡╨╝ ╤Д╨░╨║╤Г╨╗╤М╤В╨╡╤В: ${firstFaculty}`);
            // POST ╨▓╤Л╨▒╨╛╤А ╤Д╨░╨║╤Г╨╗╤М╤В╨╡╤В╨░ тАФ ╨╖╨░╨▓╨╡╤А╤И╨░╨╡╤В ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤О
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

            this._checkCloudflare(selectRes, "╨Т╤Л╨▒╨╛╤А ╤Д╨░╨║╤Г╨╗╤М╤В╨╡╤В╨░ (POST /)");

            const newerCookie = this._extractSessionCookie(selectRes.headers['set-cookie']);
            if (newerCookie) sessionCookie = newerCookie;

            // ╨Ф╨╡╨╗╨░╨╡╨╝ GET ╨╖╨░╨┐╤А╨╛╤Б ╨╜╨░ stud.php, ╤З╤В╨╛╨▒╤Л ╨╖╨░╨▓╨╡╤А╤И╨╕╤В╤М ╤Б╨╡╤Б╤Б╨╕╤О ╨▓╤Е╨╛╨┤╨░ (╨╕╨╝╨╕╤В╨╕╤А╤Г╨╡╨╝ ╤А╨╡╨┤╨╕╤А╨╡╨║╤В ╨▒╤А╨░╤Г╨╖╨╡╤А╨░)
            log.info(`[KsuAuth _tryAuth] ╨Я╨╡╤А╨╡╤Е╨╛╨┤ ╨╜╨░ stud.php ╨┤╨╗╤П ╨╖╨░╨▓╨╡╤А╤И╨╡╨╜╨╕╤П ╤Б╨╡╤Б╤Б╨╕╨╕`);
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
            this._checkCloudflare(studRes, "╨и╨░╨│ 3.5 (GET stud.php)");
            
            const newestCookie = this._extractSessionCookie(studRes.headers['set-cookie']);
            if (newestCookie) sessionCookie = newestCookie;
        } else {
            log.warn(`[KsuAuth _tryAuth] ╨б╨┐╨╕╤Б╨╛╨║ ╤Д╨░╨║╤Г╨╗╤М╤В╨╡╤В╨╛╨▓ ╨╜╨╡ ╨╜╨░╨╣╨┤╨╡╨╜ ╨╜╨░ ╨│╨╗╨░╨▓╨╜╨╛╨╣ ╤Б╤В╤А╨░╨╜╨╕╤Ж╨╡! ╨б╤В╨░╤В╤Г╤Б ╨╛╤В╨▓╨╡╤В╨░: ${mainRes.status}. ╨Ф╨╗╨╕╨╜╨░ body: ${bodyText.length}. ╨Э╨░╤З╨░╨╗╨╛ body: ${bodyText.substring(0, 300).replace(/\s+/g, ' ')}`);
        }

        // ╨и╨░╨│ 4: ╨Т╨╡╤А╨╕╤Д╨╕╨║╨░╤Ж╨╕╤П тАФ ╨┐╤А╨╛╨▓╨╡╤А╨╕╤В╤М ╤З╤В╨╛ ╨║╤Г╨║╨░ ╨┤╨╡╨╣╤Б╤В╨▓╨╕╤В╨╡╨╗╤М╨╜╨╛ ╨┤╨░╤С╤В ╨┤╨╛╤Б╤В╤Г╨┐ ╨║ ╤А╨░╤Б╨┐╨╕╤Б╨░╨╜╨╕╤О
        log.info(`[KsuAuth _tryAuth] ╨и╨░╨│ 4: ╨Т╨╡╤А╨╕╤Д╨╕╨║╨░╤Ж╨╕╤П ╤Б╨╡╤Б╤Б╨╕╨╕`);
        const verified = await this._verifyCookie(sessionCookie, proxy);
        if (!verified) {
            throw new Error("╨Ъ╤Г╨║╨░ ╨╜╨╡ ╨┐╤А╨╛╤И╨╗╨░ ╨▓╨╡╤А╨╕╤Д╨╕╨║╨░╤Ж╨╕╤О тАФ ╤А╨░╤Б╨┐╨╕╤Б╨░╨╜╨╕╨╡ ╨╜╨╡╨┤╨╛╤Б╤В╤Г╨┐╨╜╨╛");
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

            const url = encodeURI(`${domain}/view1.php?id=5044&Otdel=╤А╤Г╤Б`);
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
                    'Referer': `${domain}/stud.php`
                }
            });

            // ╨Х╤Б╨╗╨╕ ╤А╨╡╨┤╨╕╤А╨╡╨║╤В тАФ ╤Б╨╡╤Б╤Б╨╕╤П ╨╜╨╡ ╨▓╨░╨╗╨╕╨┤╨╜╨░
            if (res.status === 302 || res.status === 301) {
                log.warn("[KsuAuth Verify] ╨Я╨╛╨╗╤Г╤З╨╡╨╜ ╤А╨╡╨┤╨╕╤А╨╡╨║╤В тАФ ╤Б╨╡╤Б╤Б╨╕╤П ╨╜╨╡╨▓╨░╨╗╨╕╨┤╨╜╨░");
                return false;
            }

            this._checkCloudflare(res, "Verify GET");

            const body = typeof res.data === 'string' ? res.data : '';
            const lowerBody = body.toLowerCase();
            
            // ╨Я╤А╨╛╨▓╨╡╤А╤П╨╡╨╝ ╤З╤В╨╛ ╨╡╤Б╤В╤М ╤В╨░╨▒╨╗╨╕╤Ж╨░ ╤Б ╤А╨░╤Б╨┐╨╕╤Б╨░╨╜╨╕╨╡╨╝
            if (lowerBody.includes('<table')) {
                return true;
            }

            log.warn(`[KsuAuth Verify] ╨б╤В╤А╨░╨╜╨╕╤Ж╨░ ╨╜╨╡ ╤Б╨╛╨┤╨╡╤А╨╢╨╕╤В ╤В╨░╨▒╨╗╨╕╤Ж╤Г ╤А╨░╤Б╨┐╨╕╤Б╨░╨╜╨╕╤П. ╨б╤В╨░╤В╤Г╤Б: ${res.status}. ╨Ф╨╗╨╕╨╜╨░: ${body.length}. ╨в╨╡╨╗╨╛: ${body.substring(0, 1500).replace(/\s+/g, ' ')}`);
            return false;
        } catch (e) {
            log.warn(`[KsuAuth Verify] ╨Ю╤И╨╕╨▒╨║╨░ ╨▓╨╡╤А╨╕╤Д╨╕╨║╨░╤Ж╨╕╨╕: ${e.message}`);
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
