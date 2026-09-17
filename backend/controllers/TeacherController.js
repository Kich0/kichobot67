import log from "../logging/logging.js";

/**
 * Заглушка (Stub) TeacherController для Express API.
 * Оригинальная версия со скрейпером сохранена в archive_legacy_scraper/TeacherController.js.
 */
class TeacherController {
    async get_all_teachers(req, res, next) {
        log.warn("[TeacherController Stub] Скрейпер преподавателей отключен (Puppeteer удален).");
        return res?.json ? res.json([]) : [];
    }
}

export default new TeacherController();