import {Router} from "express";
import GptAssistantController from "../controllers/GptAssistantController.js";
import authMiddleware from "../middlewares/authMiddleware.js";

export const gptAssistantRouter = new Router()

gptAssistantRouter.post('/getAnswer', authMiddleware, GptAssistantController.getAnswerByScreenshot)