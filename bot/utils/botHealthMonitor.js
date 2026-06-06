import log from "../logging/logging.js";
import db from "../db/connection.js";

class BotHealthMonitor {
    constructor() {
        this.lastBotActivity = Date.now();
        this.startTime = Date.now();
    }
    updateActivity() {
        this.lastBotActivity = Date.now();
    }
    getTimeSinceLastActivity() {
        return Math.floor((Date.now() - this.lastBotActivity) / 1000);
    }
    getUptime() {
        return Math.floor((Date.now() - this.startTime) / 1000);
    }
    async isHealthy() {
        const checks = {
            bot: false,
            database: false,
            uptime: this.getUptime(),
            timeSinceLastActivity: this.getTimeSinceLastActivity(),
        };
        const timeSinceActivity = this.getTimeSinceLastActivity();
        if (timeSinceActivity < 300) { // 5 minutes
            checks.bot = true;
        } else {
            log.warn(`Bot health check: No activity for ${timeSinceActivity} seconds`);
        }
        try {
            if (db.connection && db.connection.readyState === 1) {
                checks.database = true;
            } else {
                log.warn('Bot health check: Database not connected');
            }
        } catch (e) {
            log.error('Bot health check: Error checking database', { stack: e.stack });
        }

        return {
            healthy: checks.bot && checks.database,
            checks,
            timestamp: new Date().toISOString(),
        };
    }
    async getStatus() {
        return this.isHealthy();
    }
}

export default new BotHealthMonitor();
