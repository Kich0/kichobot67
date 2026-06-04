import i18next from 'i18next';

import fs from 'fs/promises'
import log from "../logging/logging.js";

import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const ruTranslation = JSON.parse(String(await fs.readFile(path.join(__dirname, 'ru.json'))));
const kzTranslation = JSON.parse(String(await fs.readFile(path.join(__dirname, 'kz.json'))));

export async function i18nextInit(){
    await i18next.init({
        lng: 'ru', // Устанавливаем язык по умолчанию
        resources: {
            ru: {translation: ruTranslation},
            kz: {translation: kzTranslation},
            // Другие языковые ресурсы по мере необходимости
        },
    })
    log.info("i18next инициализирован!")
}