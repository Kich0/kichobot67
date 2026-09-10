import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

import fs from 'fs';

const rootEnvPath = path.resolve(__dirname, '.env');
const botEnvPath = path.resolve(__dirname, 'bot', '.env');

if (fs.existsSync(rootEnvPath)) {
    dotenv.config({ path: rootEnvPath });
    console.log(`[Config] Environment variables loaded from root: ${rootEnvPath}`);
} else if (fs.existsSync(botEnvPath)) {
    dotenv.config({ path: botEnvPath });
    console.log(`[Config] Environment variables loaded from bot: ${botEnvPath}`);
}

if (!process.env.DB_URI) {
    console.error("[ERROR] DB_URI is still undefined after loading .env!");
}
