import {Router} from "express";
import BrowserController from "../controllers/BrowserController.js";

export const browserRouter = new Router()

browserRouter.get("/restart_browser", BrowserController.restartBrowser)
browserRouter.post("/makeHtmlScreenShot", BrowserController.allChecksCall, BrowserController.makeHtmlScreenShot)