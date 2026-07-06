import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import log from "../logging/logging.js";

const POOL_SIZE = 8;              // Сколько прокси держать в пуле
const MAINTAIN_INTERVAL = 30 * 1000; // Проверка пула каждые 30 сек
const PROXY_TEST_TIMEOUT = 2500;  // Жёсткий таймаут для теста — только быстрые прокси
const BATCH_SIZE = 30;            // Параллельная проверка батчами

class FreeProxyService {
    constructor() {
        this.cachedProxies = [];
        this.proxyIndex = 0;
        this.proxyPool = [];          // Пул рабочих прокси
        this.roundRobinIndex = 0;     // Индекс для round-robin раздачи
        this.isInitialized = false;
        this.isMaintaining = false;
    }

    async initPool() {
        if (this.isInitialized) return;
        log.info(`[ProxyPool] Инициализация пула (цель: ${POOL_SIZE} прокси)...`);

        try {
            await this._fillPool();
            this.isInitialized = true;
            log.info(`[ProxyPool] Пул готов: ${this.proxyPool.length}/${POOL_SIZE} прокси`);
            this._startMaintenance();
        } catch (e) {
            log.error(`[ProxyPool] Ошибка инициализации пула: ${e.message}`);
            this.isInitialized = true; // Помечаем как инициализированный, чтобы запустить maintenance
            this._startMaintenance();
        }
    }

    _startMaintenance() {
        setInterval(async () => {
            if (this.isMaintaining) {
                return;
            }
            this.isMaintaining = true;

            try {
                log.info(`[ProxyPool Maintain] Проверяю здоровье пула (${this.proxyPool.length}/${POOL_SIZE})...`);
                const healthChecks = await Promise.all(
                    this.proxyPool.map(async (proxy) => {
                        const alive = await this.testProxy(proxy);
                        return { proxy, alive };
                    })
                );
                const aliveProxies = healthChecks.filter(r => r.alive).map(r => r.proxy);
                const deadCount = this.proxyPool.length - aliveProxies.length;

                if (deadCount > 0) {
                    log.info(`[ProxyPool Maintain] Мёртвых прокси: ${deadCount}. Ищу замену...`);
                }

                this.proxyPool = aliveProxies;
                // Корректируем round-robin индекс
                if (this.roundRobinIndex >= this.proxyPool.length) {
                    this.roundRobinIndex = 0;
                }

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

    async _fillPool() {
        const needed = POOL_SIZE - this.proxyPool.length;
        if (needed <= 0) return;

        log.info(`[ProxyPool Fill] Нужно ещё ${needed} прокси...`);
        if (this.cachedProxies.length === 0 || this.proxyIndex >= this.cachedProxies.length) {
            this.cachedProxies = await this.getProxies();
            this.proxyIndex = 0;
        }

        if (this.cachedProxies.length === 0) {
            log.error("[ProxyPool Fill] Список прокси пуст!");
            return;
        }
        const poolSet = new Set(this.proxyPool);

        let found = 0;
        let checkedCount = 0;
        const maxChecks = 600;

        while (found < needed && this.proxyIndex < this.cachedProxies.length && checkedCount < maxChecks) {
            const batch = this.cachedProxies
                .slice(this.proxyIndex, this.proxyIndex + BATCH_SIZE)
                .filter(p => !poolSet.has(p));

            if (batch.length > 0) {
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

        // Источник 1: ProxyScrape (ssl=yes)
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

        // Источник 2: ProxyScrape (ssl=all)
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

        // Источник 3: GitHub TheSpeedX
        try {
            const res3 = await axios.get(
                "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
                { timeout: 10000 }
            );
            const list3 = res3.data.split('\n').map(p => p.trim()).filter(p => p.length > 0 && p.includes(':'));
            log.info(`GitHub TheSpeedX: ${list3.length} прокси`);
            allProxies.push(...list3);
        } catch (e) {
            log.error("Ошибка GitHub TheSpeedX: " + e.message);
        }

        // Источник 4: GitHub monosans
        try {
            const res4 = await axios.get(
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
                { timeout: 10000 }
            );
            const list4 = res4.data.split('\n').map(p => p.trim()).filter(p => p.length > 0 && p.includes(':'));
            log.info(`GitHub monosans: ${list4.length} прокси`);
            allProxies.push(...list4);
        } catch (e) {
            log.error("Ошибка GitHub monosans: " + e.message);
        }

        allProxies = [...new Set(allProxies)];
        allProxies.sort(() => Math.random() - 0.5);

        log.info(`Итого уникальных прокси: ${allProxies.length}`);
        return allProxies;
    }

    async testProxy(proxy) {
        try {
            const httpsAgent = new HttpsProxyAgent(`http://${proxy}`, { rejectUnauthorized: false });

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

            if (res.status !== 200) return false;

            if (res.data && typeof res.data === 'string') {
                const body = res.data;
                const isKSU = body.includes('buketov') || 
                              body.includes('schedule') || 
                              body.includes('login') ||
                              body.includes('авторизация') ||
                              body.includes('Авторизация') ||
                              body.includes('пайдаланушы') ||
                              body.includes('URL=login.php');
                const isCloudflare = body.includes('Cloudflare') || body.includes('Just a moment');
                
                // Жёсткий отброс Cloudflare — axios его не пройдёт
                if (isCloudflare) return false;
                if (isKSU) return true;
            }
            return false;
        } catch (e) {
            return false;
        }
    }

    /**
     * Round-robin: выдаёт следующий прокси из пула, НЕ удаляя его.
     * Возвращает null если пул пуст.
     */
    getNextProxy() {
        if (this.proxyPool.length === 0) {
            log.warn("[ProxyPool] Пул пуст!");
            return null;
        }
        if (this.roundRobinIndex >= this.proxyPool.length) {
            this.roundRobinIndex = 0;
        }
        const proxy = this.proxyPool[this.roundRobinIndex];
        this.roundRobinIndex++;
        return proxy;
    }

    /**
     * Пометить прокси как мёртвый — удалить из пула.
     */
    markProxyDead(proxy) {
        const idx = this.proxyPool.indexOf(proxy);
        if (idx !== -1) {
            this.proxyPool.splice(idx, 1);
            log.info(`[ProxyPool] Удалён мёртвый прокси: ${proxy} (осталось: ${this.proxyPool.length})`);
            // Корректируем round-robin индекс
            if (this.roundRobinIndex > idx) {
                this.roundRobinIndex--;
            }
            if (this.roundRobinIndex >= this.proxyPool.length) {
                this.roundRobinIndex = 0;
            }
            // Запускаем пополнение в фоне
            if (this.proxyPool.length < POOL_SIZE) {
                this._fillPool().catch(e => log.error("[ProxyPool] Ошибка автопополнения: " + e.message));
            }
        }
    }

    /**
     * Старый метод для обратной совместимости (shift из пула).
     * Используется в BrowserController.auth() для Puppeteer.
     */
    async getWorkingProxy() {
        if (this.proxyPool.length > 0) {
            const proxy = this.proxyPool.shift();
            log.info(`[ProxyPool] Выдан прокси из пула (shift): ${proxy} (осталось: ${this.proxyPool.length})`);
            if (this.proxyPool.length < POOL_SIZE) {
                this._fillPool().catch(e => log.error("[ProxyPool] Ошибка автопополнения: " + e.message));
            }
            return proxy;
        }

        log.warn("[ProxyPool] Пул пуст! Ищу прокси вручную...");
        if (this.cachedProxies.length === 0 || this.proxyIndex >= this.cachedProxies.length) {
            this.cachedProxies = await this.getProxies();
            this.proxyIndex = 0;
        }

        if (this.cachedProxies.length === 0) {
            log.error("Список бесплатных прокси пуст!");
            return null;
        }

        let checkedCount = 0;
        const maxAttempts = 300;

        while (this.proxyIndex < this.cachedProxies.length && checkedCount < maxAttempts) {
            const batch = this.cachedProxies.slice(this.proxyIndex, this.proxyIndex + BATCH_SIZE);

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

        log.error(`❌ Проверено ${checkedCount} прокси — ни один не смог открыть КарГУ.`);
        return null;
    }
}

export default new FreeProxyService();
