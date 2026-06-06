import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import log from "../logging/logging.js";

const POOL_SIZE = 5;              // Сколько прокси держать в пуле
const MAINTAIN_INTERVAL = 3 * 60 * 1000; // Проверка пула каждые 3 минуты
const PROXY_TEST_TIMEOUT = 7000;  // Таймаут теста одного прокси
const BATCH_SIZE = 10;            // СНИЖЕНО ДО 10: Параллельная проверка батчами (чтобы не было Out of Memory на 512MB)

class FreeProxyService {
    constructor() {
        this.cachedProxies = [];
        this.proxyIndex = 0;
        this.proxyPool = [];          // Пул рабочих прокси
        this.isInitialized = false;
        this.isMaintaining = false;   // Флаг чтобы не запускать параллельное обслуживание
    }

    /**
     * Инициализация пула при старте.
     * Вызывается один раз из BrowserController.
     */
    async initPool() {
        if (this.isInitialized) return;
        log.info(`[ProxyPool] Инициализация пула (цель: ${POOL_SIZE} прокси)...`);

        try {
            await this._fillPool();
            this.isInitialized = true;
            log.info(`[ProxyPool] Пул готов: ${this.proxyPool.length}/${POOL_SIZE} прокси`);

            // Запускаем фоновое обслуживание
            this._startMaintenance();
        } catch (e) {
            log.error(`[ProxyPool] Ошибка инициализации пула: ${e.message}`);
        }
    }

    /**
     * Фоновое обслуживание пула каждые MAINTAIN_INTERVAL мс.
     */
    _startMaintenance() {
        setInterval(async () => {
            if (this.isMaintaining) {
                log.info("[ProxyPool Maintain] Уже идёт обслуживание, пропускаю.");
                return;
            }
            this.isMaintaining = true;

            try {
                log.info(`[ProxyPool Maintain] Проверяю здоровье пула (${this.proxyPool.length}/${POOL_SIZE})...`);

                // Проверяем каждый прокси в пуле параллельно
                const healthChecks = await Promise.all(
                    this.proxyPool.map(async (proxy) => {
                        const alive = await this.testProxy(proxy);
                        return { proxy, alive };
                    })
                );

                // Оставляем только живые
                const aliveProxies = healthChecks.filter(r => r.alive).map(r => r.proxy);
                const deadCount = this.proxyPool.length - aliveProxies.length;

                if (deadCount > 0) {
                    log.info(`[ProxyPool Maintain] Мёртвых прокси: ${deadCount}. Ищу замену...`);
                }

                this.proxyPool = aliveProxies;

                // Дозаполняем пул если нужно
                if (this.proxyPool.length < POOL_SIZE) {
                    await this._fillPool();
                }

                log.info(`[ProxyPool Maintain] Готово. Пул: ${this.proxyPool.length}/${POOL_SIZE}`);
            } catch (e) {
                log.error(`[ProxyPool Maintain] Ошибка: ${e.message}`);
            } finally {
                this.isMaintaining = false;
            }
        }, MAINTAIN_INTERVAL);
    }

    /**
     * Дозаполняет пул до POOL_SIZE рабочими прокси.
     */
    async _fillPool() {
        const needed = POOL_SIZE - this.proxyPool.length;
        if (needed <= 0) return;

        log.info(`[ProxyPool Fill] Нужно ещё ${needed} прокси...`);

        // Скачиваем новый список если кэш пуст или исчерпан
        if (this.cachedProxies.length === 0 || this.proxyIndex >= this.cachedProxies.length) {
            this.cachedProxies = await this.getProxies();
            this.proxyIndex = 0;
        }

        if (this.cachedProxies.length === 0) {
            log.error("[ProxyPool Fill] Список прокси пуст!");
            return;
        }

        // Исключаем из кэша то что уже в пуле
        const poolSet = new Set(this.proxyPool);

        let found = 0;
        let checkedCount = 0;
        const maxChecks = 200; // Не проверяем больше 200 за один вызов

        while (found < needed && this.proxyIndex < this.cachedProxies.length && checkedCount < maxChecks) {
            const batch = this.cachedProxies
                .slice(this.proxyIndex, this.proxyIndex + BATCH_SIZE)
                .filter(p => !poolSet.has(p));

            if (batch.length > 0) {
                log.info(`[ProxyPool Fill] Тестирую батч ${this.proxyIndex + 1}-${this.proxyIndex + batch.length}...`);

                const results = await Promise.all(
                    batch.map(async (proxy) => {
                        const isWorking = await this.testProxy(proxy);
                        return { proxy, isWorking };
                    })
                );

                for (const r of results) {
                    if (r.isWorking && found < needed && !poolSet.has(r.proxy)) {
                        this.proxyPool.push(r.proxy);
                        poolSet.add(r.proxy);
                        found++;
                        log.info(`[ProxyPool Fill] +1 прокси: ${r.proxy} (пул: ${this.proxyPool.length}/${POOL_SIZE})`);
                    }
                }
            }

            this.proxyIndex += BATCH_SIZE;
            checkedCount += BATCH_SIZE;
        }

        if (found < needed) {
            log.warn(`[ProxyPool Fill] Нашли только ${found}/${needed}. Пул: ${this.proxyPool.length}/${POOL_SIZE}`);
        }
    }

    async getProxies() {
        log.info("Скачиваю списки бесплатных прокси из нескольких источников...");
        let allProxies = [];

        // Источник 1: ProxyScrape — только HTTPS-совместимые
        try {
            const res1 = await axios.get(
                "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=yes&anonymity=all",
                { timeout: 10000 }
            );
            const list1 = res1.data.split('\n').map(p => p.trim()).filter(p => p.length > 0);
            log.info(`ProxyScrape (ssl=yes): ${list1.length} прокси`);
            allProxies.push(...list1);
        } catch (e) {
            log.error("Ошибка ProxyScrape: " + e.message);
        }

        // Источник 2: ProxyScrape — все прокси (ssl=all), на случай если ssl=yes мало
        try {
            const res2 = await axios.get(
                "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all",
                { timeout: 10000 }
            );
            const list2 = res2.data.split('\n').map(p => p.trim()).filter(p => p.length > 0);
            log.info(`ProxyScrape (ssl=all): ${list2.length} прокси`);
            allProxies.push(...list2);
        } catch (e) {
            log.error("Ошибка ProxyScrape (all): " + e.message);
        }

        // Убираем дубликаты
        allProxies = [...new Set(allProxies)];
        // Перемешиваем случайно, чтобы не застревать на одних и тех же мертвых
        allProxies.sort(() => Math.random() - 0.5);

        log.info(`Итого уникальных прокси: ${allProxies.length}`);
        return allProxies;
    }

    async testProxy(proxy) {
        try {
            const httpsAgent = new HttpsProxyAgent(`http://${proxy}`);

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), PROXY_TEST_TIMEOUT + 1000);

            const res = await axios.get("https://schedule.buketov.edu.kz/", {
                httpsAgent,
                httpAgent: new HttpProxyAgent(`http://${proxy}`),
                proxy: false,
                timeout: PROXY_TEST_TIMEOUT,
                signal: controller.signal,
                validateStatus: status => true,
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            });
            clearTimeout(timeoutId);

            // Проверяем что ответ РЕАЛЬНО от КСУ, а не от самого прокси
            if (res.data && typeof res.data === 'string') {
                const body = res.data;
                const isKSU = body.includes('buketov') || 
                              body.includes('schedule') || 
                              body.includes('login') ||
                              body.includes('авторизация') ||
                              body.includes('Авторизация') ||
                              body.includes('пайдаланушы');
                if (isKSU) {
                    return true;
                }
            }
            return false;
        } catch (e) {
            return false;
        }
    }

    /**
     * Возвращает рабочий прокси из пула (мгновенно).
     * Если пул пуст — ищет вручную (старый путь).
     */
    async getWorkingProxy() {
        // Если в пуле есть прокси — забираем первый
        if (this.proxyPool.length > 0) {
            const proxy = this.proxyPool.shift();
            log.info(`[ProxyPool] Выдан прокси из пула: ${proxy} (осталось: ${this.proxyPool.length})`);
            return proxy;
        }

        // Пул пуст — ищем вручную (fallback)
        log.warn("[ProxyPool] Пул пуст! Ищу прокси вручную...");

        // Скачиваем новый список если кэш пуст или исчерпан
        if (this.cachedProxies.length === 0 || this.proxyIndex >= this.cachedProxies.length) {
            this.cachedProxies = await this.getProxies();
            this.proxyIndex = 0;
        }

        if (this.cachedProxies.length === 0) {
            log.error("Список бесплатных прокси пуст!");
            return null;
        }

        log.info(`В кэше ${this.cachedProxies.length - this.proxyIndex} непроверенных прокси. Начинаю проверку...`);

        let checkedCount = 0;
        const maxAttempts = 300;

        while (this.proxyIndex < this.cachedProxies.length && checkedCount < maxAttempts) {
            const batch = this.cachedProxies.slice(this.proxyIndex, this.proxyIndex + BATCH_SIZE);
            log.info(`Тестирую батч прокси ${this.proxyIndex + 1} - ${this.proxyIndex + batch.length} из ${this.cachedProxies.length}...`);

            const results = await Promise.all(
                batch.map(async (proxy) => {
                    const isWorking = await this.testProxy(proxy);
                    return { proxy, isWorking };
                })
            );

            this.proxyIndex += batch.length;
            checkedCount += batch.length;

            const workingProxy = results.find(r => r.isWorking);
            if (workingProxy) {
                log.info(`✅ Найден рабочий прокси: ${workingProxy.proxy}`);
                return workingProxy.proxy;
            }
        }

        log.error(`❌ Проверено ${checkedCount} прокси — ни один не смог открыть КСУ.`);
        return null;
    }

    /**
     * Возвращает статус пула для мониторинга.
     */
    getPoolStatus() {
        return {
            poolSize: this.proxyPool.length,
            targetSize: POOL_SIZE,
            cachedTotal: this.cachedProxies.length,
            cachedRemaining: this.cachedProxies.length - this.proxyIndex,
            isInitialized: this.isInitialized,
            isMaintaining: this.isMaintaining
        };
    }
}

export default new FreeProxyService();
