import {Router} from "express";
import BrowserController from "../controllers/BrowserController.js";
import authMiddleware from "../middlewares/authMiddleware.js";

export const browserRouter = new Router()

browserRouter.get("/restart_browser", authMiddleware, BrowserController.restartBrowser)