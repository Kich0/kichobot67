import cron from "node-cron";
import BrowserController from "../controllers/BrowserController.js";
import log from "../logging/logging.js";

export async function setupKsuReAuth(){
    cron.schedule('0 * * * *', async () => {
        if (BrowserController.isAuthing || BrowserController.isRecovering || BrowserController.isLaunching) {
            log.info("[Cron ReAuth] Пропускаю — уже идёт авторизация/восстановление/запуск браузера");
            return;
        }
        await BrowserController.auth()
    });
}