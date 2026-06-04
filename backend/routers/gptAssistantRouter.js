import {Router} from "express";
import GptAssistantController from "../controllers/GptAssistantController.js";

export const gptAssistantRouter = new Router()

gptAssistantRouter.post('/getAnswer', GptAssistantController.getAnswerByScreenshot)