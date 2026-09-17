import log from "../logging/logging.js";

/**
 * Заглушка (Stub) BrowserController.
 * Настоящая версия с Puppeteer перенесена в archive_legacy_scraper/BrowserController.js.
 * Puppeteer и Chromium полностью удалены из зависимостей бота,
 * так как всё расписание работает через официальный REST API КарУ (BuketovApiService).
 */
class BrowserController {
    browser = null;
    auth_cookie = "";
    faculties_data = [];
    isAuthing = false;
    isRecovering = false;
    isLaunching = false;

    constructor() {
        // Браузер отключен, Puppeteer не загружается
    }

    async _waitForAuth() {
        return true;
    }

    allChecksCall = async (req, res, next) => {
        // Пропускаем запрос дальше без проверки браузера
        return next();
    };

    auth = async () => {
        log.info("[BrowserController Stub] Авторизация браузера не требуется: бот использует официальный REST API КарУ.");
        return true;
    };

    launchBrowser = async () => {
        log.info("[BrowserController Stub] Запуск браузера отключен в этой версии.");
        return null;
    };

    restartBrowser = async (req, res) => {
        return res?.json({
            status: "disabled",
            message: "Браузер и Puppeteer отключены. Бот работает на официальном REST API КарУ."
        });
    };

    closeBrowser = async () => {
        return true;
    };
}

export default new BrowserController();