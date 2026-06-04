import OpenAI from "openai";

const openai = new OpenAI();

class GptAssistantService{
    async getAnswerByScreenshot(newFileName) {
        const response = await openai.chat.completions.create({
            model: "gpt-4o",
            messages: [
                {
                    role: "user",
                    content: [
                        {
                            type: "text",
                            text: "Помоги с тестом по Big Data, какой единственный верный вариант ответа? Отвечай исключительно одной цифрой - порядковым номером варианта ответа и ничего более."
                        },
                        {
                            type: "image_url",
                            image_url: {
                                "url": `https://api.tolyan.me/express/api/gpt-input-pictures/${newFileName}`,
                            },
                        },
                    ],
                },
            ],
            max_tokens:30,
        });
        return response

    }
}

export default new GptAssistantService();