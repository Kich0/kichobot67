import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
import log from "../logging/logging.js";

class FreeProxyService {
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
        const proxies = await this.getProxies();
        
        if (proxies.length === 0) {
            log.error("Список бесплатных прокси пуст!");
            return null;
        }

        log.info(`Скачано ${proxies.length} бесплатных прокси. Начинаю асинхронную проверку первых 50 шт...`);

        // Тестируем прокси батчами по 10 штук параллельно, чтобы ускорить процесс
        const maxProxiesToTest = Math.min(proxies.length, 50);
        const proxiesToTest = proxies.slice(0, maxProxiesToTest);
        
        const batchSize = 10;
        for (let i = 0; i < proxiesToTest.length; i += batchSize) {
            const batch = proxiesToTest.slice(i, i + batchSize);
            log.info(`Тестирую батч прокси ${i + 1} - ${i + batch.length}...`);
            
            const results = await Promise.all(
                batch.map(async (proxy) => {
                    const isWorking = await this.testProxy(proxy);
                    return { proxy, isWorking };
                })
            );
            
            const workingProxy = results.find(r => r.isWorking);
            if (workingProxy) {
                log.info(`✅ Найден рабочий прокси: ${workingProxy.proxy}`);
                return workingProxy.proxy;
            }
        }
        
        log.error("❌ Не удалось найти рабочий бесплатный прокси среди проверенных.");
        return null;
    }
}

export default new FreeProxyService();
