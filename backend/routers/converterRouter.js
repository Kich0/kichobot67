import {Router} from "express";
import {fileCheckMiddleware, upload, wordToHtml} from "../controllers/ConverterController.js";

export const converterRouter = new Router()

converterRouter.post('/word-to-html', upload.single('file'), fileCheckMiddleware, wordToHtml)