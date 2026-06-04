import BrowserController from "../controllers/BrowserController.js";
import fs from "fs";

class BrowserService {
    async restartBrowser() {
        try {
            await BrowserController.browser?.close()
            await BrowserController.launchBrowser()
        } catch (e) {
            throw e
        }
    }

    async getScreenshotBufferByHtml(htmlCode) {
        const page = await BrowserController.browser.newPage();
        const tempHtml = `<html><style>#screen-container { display: inline-block; padding: 10px;} </style> <body><div id="screen-container">${htmlCode}</div></body></html>`;
        try {
            await page.setContent(tempHtml);

            const element = await page.$(`#screen-container`);

            await element.screenshot({path: 'screenshot.png'})
            return await fs.promises.readFile('screenshot.png')
        } catch (e) {
            throw e
        } finally {
            await page.close()
        }
    }
}

export default new BrowserService()