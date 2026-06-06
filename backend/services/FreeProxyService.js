import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import log from "../logging/logging.js";

class FreeProxyService {
    constructor() {
        this.cachedProxies = [];
        this.proxyIndex = 0;
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
            const timeoutId = setTimeout(() => controller.abort(), 8000);

            const res = await axios.get("https://schedule.buketov.edu.kz/", {
                httpsAgent,
                httpAgent: new HttpProxyAgent(`http://${proxy}`),
                proxy: false,
                timeout: 7000,
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
                    log.info(`[Proxy OK] ${proxy} -> status=${res.status}, bodyLen=${body.length}, КСУ=ДА`);
                    return true;
                }
            }
            return false;
        } catch (e) {
            return false;
        }
    }

    async getWorkingProxy() {
        log.info("Начинаю поиск бесплатного рабочего прокси...");

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

        const batchSize = 30; // Больший батч для скорости
        const maxAttempts = 300; // Проверяем до 300 штук за вызов
        let checkedCount = 0;

        while (this.proxyIndex < this.cachedProxies.length && checkedCount < maxAttempts) {
            const batch = this.cachedProxies.slice(this.proxyIndex, this.proxyIndex + batchSize);
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

        log.error(`❌ Проверено ${checkedCount} прокси — ни один не смог открыть КСУ. В следующий раз продолжу с ${this.proxyIndex}.`);
        return null;
    }
}

export default new FreeProxyService();
