import cron from "node-cron";
import BrowserController from "../controllers/BrowserController.js";

export async function setupKsuReAuth(){
    cron.schedule('0 * * * *', async () => {
        await BrowserController.auth()
    });
}