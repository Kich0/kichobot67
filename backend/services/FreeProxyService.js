import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import log from "../logging/logging.js";

class FreeProxyService {
    constructor() {
        this.cachedProxies = [];
        this.proxyIndex = 0;
    }

    async getProxies() {
        try {
            log.info("Скачиваю список бесплатных прокси...");
            const res = await axios.get("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all", { timeout: 10000 });
            const proxies = res.data.split('\n').map(p => p.trim()).filter(p => p.length > 0);
            return proxies;
        } catch (e) {
            log.error("Ошибка при скачивании списка прокси: " + e.message);
            return [];
        }
    }

    async testProxy(proxy) {
        try {
            const httpsAgent = new HttpsProxyAgent(`http://${proxy}`);
            
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 6000);
            
            const res = await axios.get("https://schedule.buketov.edu.kz/", {
                httpsAgent,
                timeout: 5000,
                signal: controller.signal,
                validateStatus: status => true
            });
            clearTimeout(timeoutId);
            
            if (res.data && res.data.includes('<TITLE>Жүйе пайдаланушысының авторизациясы</TITLE>')) {
                return true;
            }
            return false;
        } catch (e) {
            return false;
        }
    }

    async getWorkingProxy() {
        log.info("Начинаю поиск бесплатного рабочего прокси...");
        
        if (this.cachedProxies.length === 0 || this.proxyIndex >= this.cachedProxies.length) {
            this.cachedProxies = await this.getProxies();
            this.proxyIndex = 0;
        }
        
        if (this.cachedProxies.length === 0) {
            log.error("Список бесплатных прокси пуст!");
            return null;
        }

        log.info(`В кэше ${this.cachedProxies.length - this.proxyIndex} непроверенных прокси. Начинаю проверку...`);

        const batchSize = 20; // Увеличил до 20 для ускорения
        const maxAttempts = 200; // Проверяем максимум 200 штук за один вызов
        let checkedCount = 0;

        while (this.proxyIndex < this.cachedProxies.length && checkedCount < maxAttempts) {
            const batch = this.cachedProxies.slice(this.proxyIndex, this.proxyIndex + batchSize);
            log.info(`Тестирую батч прокси ${this.proxyIndex + 1} - ${this.proxyIndex + batch.length}...`);
            
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
        
        log.error("❌ Не удалось найти рабочий бесплатный прокси среди проверенных батчей. В следующем запросе проверка продолжится.");
        return null;
    }
}

export default new FreeProxyService();
