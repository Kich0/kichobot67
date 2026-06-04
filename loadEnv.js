import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const envPath = path.resolve(__dirname, 'bot', '.env');
dotenv.config({ path: envPath });

console.log(`[Config] Environment variables loaded from: ${envPath}`);
if (!process.env.DB_URI) {
    console.error("[ERROR] DB_URI is still undefined after loading .env!");
}
