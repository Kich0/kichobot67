import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import log from "../logging/logging.js";
import config from "../config.js";
import FreeProxyService from "./FreeProxyService.js";

const COOKIE_TTL = 30 * 60 * 1000; // 30 минут
const AUTH_TIMEOUT = 10000; // 10 сек на авторизацию
const VERIFY_TIMEOUT = 8000; // 8 сек на проверку куки

class KsuAuthService {
    constructor() {
        this._cookie = null;
        this._cookieExpiry = 0;
        this._authPromise = null; // дедупликация
    }

    /**
     * Получить валидную куку PHPSESSID.
     * Кэширует куку на 30 минут. При конкурентных вызовах — один промис.
     */
    async getCookie() {
        if (this._cookie && Date.now() < this._cookieExpiry) {
            return this._cookie;
        }
        // Дедупликация: если auth уже идёт — ждём тот же промис
        if (this._authPromise) {
            log.info("[KsuAuth] Уже идёт авторизация, жду...");
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
     * Сбросить кэш куки (вызывается при 302 или ошибке сессии)
     */
    invalidate() {
        log.info("[KsuAuth] Кэш куки сброшен");
        this._cookie = null;
        this._cookieExpiry = 0;
    }

    /**
     * Авторизация на КарГУ через axios POST.
     * Стратегия: сначала напрямую, потом через прокси.
     */
    async _authorize() {
        log.info("[KsuAuth] Начинаю авторизацию...");

        // Попытка 1: напрямую (без прокси) — если DNS на Render работает, это самый быстрый путь
        try {
            const cookie = await this._tryAuth(null);
            if (cookie) {
                log.info("[KsuAuth] ✅ Авторизация без прокси успешна!");
                this._cookie = cookie;
                this._cookieExpiry = Date.now() + COOKIE_TTL;
                return cookie;
            }
        } catch (e) {
            log.warn(`[KsuAuth] Прямая авторизация не удалась: ${e.message}`);
        }

        // Попытки 2-6: через прокси из пула
        const maxAttempts = 5;
        let lastError = null;
        for (let i = 0; i < maxAttempts; i++) {
            const proxy = await FreeProxyService.getNextProxy();
            if (!proxy) {
                log.warn(`[KsuAuth] Попытка ${i + 1}/${maxAttempts}: нет прокси в пуле`);
                await new Promise(r => setTimeout(r, 1000));
                continue;
            }
            try {
                log.info(`[KsuAuth] Попытка ${i + 1}/${maxAttempts} через прокси: ${proxy}`);
                const cookie = await this._tryAuth(proxy);
                if (cookie) {
                    log.info(`[KsuAuth] ✅ Авторизация через прокси ${proxy} успешна!`);
                    this._cookie = cookie;
                    this._cookieExpiry = Date.now() + COOKIE_TTL;
                    return cookie;
                }
            } catch (e) {
                lastError = e;
                log.warn(`[KsuAuth] Попытка ${i + 1}/${maxAttempts} не удалась: ${e.message}`);
                FreeProxyService.markProxyDead(proxy);
            }
        }

        log.error("[KsuAuth] ❌ Все попытки авторизации исчерпаны");
        throw lastError || new Error("Не удалось авторизоваться на КарГУ");
    }

    /**
     * Одна попытка авторизации.
     * @param {string|null} proxy - прокси (null = прямое подключение)
     * @returns {string|null} - кука "PHPSESSID=xxx" или null
     */
    async _tryAuth(proxy) {
        const domain = config.KSU_DOMAIN;
        const agentOpts = proxy ? { rejectUnauthorized: false } : undefined;
        const httpsAgent = proxy ? new HttpsProxyAgent(`http://${proxy}`, agentOpts) : undefined;
        const httpAgent = proxy ? new HttpProxyAgent(`http://${proxy}`) : undefined;

        log.info(`[KsuAuth _tryAuth] Шаг 1: GET login.php`);
        // Шаг 1: GET login.php — получить начальную сессию
        const loginPageRes = await axios.get(`${domain}/login.php`, {
            httpsAgent,
            httpAgent,
            proxy: false,
            timeout: AUTH_TIMEOUT,
            maxRedirects: 0,
            validateStatus: s => s < 400,
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        });

        // Извлекаем PHPSESSID из set-cookie заголовка
        let sessionCookie = this._extractSessionCookie(loginPageRes.headers['set-cookie']);
        if (!sessionCookie) {
            throw new Error("Сервер не вернул PHPSESSID на шаге 1");
        }
        log.info(`[KsuAuth _tryAuth] Шаг 1 успешен, cookie: ${sessionCookie}`);

        log.info(`[KsuAuth _tryAuth] Шаг 2: POST login.php`);
        // Шаг 2: POST login.php — отправить логин/пароль
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
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Cookie': sessionCookie
                }
            }
        );

        // Если прилетел новый Set-Cookie, обновляем
        const newCookie = this._extractSessionCookie(loginRes.headers['set-cookie']);
        if (newCookie) {
            sessionCookie = newCookie;
            log.info(`[KsuAuth _tryAuth] Шаг 2 выдал новый cookie: ${sessionCookie}`);
        }

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
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Cookie': sessionCookie
            }
        });

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
            const redirectRes = await axios.get(redirectUrl, {
                httpsAgent,
                httpAgent,
                proxy: false,
                timeout: AUTH_TIMEOUT,
                maxRedirects: 0,
                validateStatus: s => s < 400,
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Cookie': sessionCookie
                }
            });
            mainBody = redirectRes.data;
        }

        // Извлечь список факультетов для POST
        const bodyText = typeof mainBody === 'string' ? mainBody : '';
        const selectMatch = bodyText.match(/<option[^>]*>([^<]+)<\/option>/);
        const firstFaculty = selectMatch ? selectMatch[1] : null;

        if (firstFaculty) {
            log.info(`[KsuAuth _tryAuth] Выбираем факультет: ${firstFaculty}`);
            // POST выбор факультета — завершает авторизацию
            const selectRes = await axios.post(`${domain}/`,
                `Login=${encodeURIComponent(firstFaculty)}`,
                {
                    httpsAgent,
                    httpAgent,
                    proxy: false,
                    timeout: AUTH_TIMEOUT,
                    maxRedirects: 0,
                    validateStatus: s => s < 500,
                    headers: {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Cookie': sessionCookie
                    }
                }
            );

            const newerCookie = this._extractSessionCookie(selectRes.headers['set-cookie']);
            if (newerCookie) sessionCookie = newerCookie;
        } else {
            log.warn("[KsuAuth _tryAuth] Список факультетов не найден на главной странице!");
        }

        // Шаг 4: Верификация — проверить что кука действительно даёт доступ к расписанию
        log.info(`[KsuAuth _tryAuth] Шаг 4: Верификация сессии`);
        const verified = await this._verifyCookie(sessionCookie, proxy);
        if (!verified) {
            throw new Error("Кука не прошла верификацию — расписание недоступно");
        }

        return sessionCookie;
    }

    /**
     * Проверить что кука даёт доступ к расписанию (GET view1.php)
     */
    async _verifyCookie(cookie, proxy) {
        try {
            const domain = config.KSU_DOMAIN;
            const httpsAgent = proxy ? new HttpsProxyAgent(`http://${proxy}`, { rejectUnauthorized: false }) : undefined;
            const httpAgent = proxy ? new HttpProxyAgent(`http://${proxy}`) : undefined;

            const url = encodeURI(`${domain}/view1.php?id=5044&Otdel=рус`);
            const res = await axios.get(url, {
                httpsAgent,
                httpAgent,
                proxy: false,
                timeout: VERIFY_TIMEOUT,
                maxRedirects: 0,
                validateStatus: () => true,
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Cookie': cookie
                }
            });

            // Если редирект — сессия не валидна
            if (res.status === 302 || res.status === 301) {
                log.warn("[KsuAuth Verify] Получен редирект — сессия невалидна");
                return false;
            }

            const body = typeof res.data === 'string' ? res.data : '';
            
            // Проверяем что есть таблица с расписанием
            if (body.includes('<table') && !body.includes('Cloudflare') && !body.includes('Forbidden')) {
                return true;
            }

            log.warn("[KsuAuth Verify] Страница не содержит таблицу расписания");
            return false;
        } catch (e) {
            log.warn(`[KsuAuth Verify] Ошибка верификации: ${e.message}`);
            return false;
        }
    }

    /**
     * Извлечь PHPSESSID из массива set-cookie заголовков
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
