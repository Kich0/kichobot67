import ApiError from "../exceptions/apiError.js";
import gptAssistantService from "../services/GptAssistantService.js";
import log from "../logging/logging.js";

class GptAssistantController {
    async getAnswerByScreenshot(req, res, next) {
        try {
            const {image} = req.body;
            if (!image) {
                return next(ApiError.BadRequest("Необходимо передать скриншот"));
            }

            const answer = await gptAssistantService.getAnswerByScreenshot(image);
            return res.json(answer);
        } catch (e) {
            log.error("Ошибка при получении ответа на скриншот: ", e);
            next(e);
        }
    }
}

export default new GptAssistantController();