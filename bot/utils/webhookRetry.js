import log from '../logging/logging.js';
import config from '../config.js';


class WebhookRetryManager {
    constructor(bot) {
        this.bot = bot;
        this.maxRetries = 20;
        this.baseDelay = 2000; // 2 seconds
        this.maxDelay = 60000; // 60 seconds
        this.retryCount = 0;
        this.isRetrying = false;
        this.retryTimer = null;
        this.webhookUrl = null;
        this.lastSuccessfulSetup = null;
    }

    
    async setWebhookWithRetry(webhookUrl) {
        this.webhookUrl = webhookUrl;
        return await this._attemptWebhookSetup();
    }

    
    async _attemptWebhookSetup() {
        try {
            await this.bot.setWebHook(this.webhookUrl);
            log.info(`Webhook set successfully: ${this.webhookUrl}`);
            const webhookInfo = await this.bot.getWebHookInfo();
            if (webhookInfo.url === this.webhookUrl) {
                log.info('Webhook verification passed', {
                    url: webhookInfo.url,
                    pending_update_count: webhookInfo.pending_update_count,
                    last_error_date: webhookInfo.last_error_date,
                    last_error_message: webhookInfo.last_error_message
                });

                this.lastSuccessfulSetup = Date.now();
                this.retryCount = 0;
                this.isRetrying = false;

                return true;
            } else {
                throw new Error(`Webhook URL mismatch. Expected: ${this.webhookUrl}, Got: ${webhookInfo.url}`);
            }
        } catch (e) {
            log.error('Failed to set webhook!', {
                stack: e.stack,
                attempt: this.retryCount + 1,
                maxRetries: this.maxRetries
            });
            if (this.retryCount === 0 && !config.DEBUG) {
                this.bot.sendMessage(config.LOG_CHANEL_ID,
                    `⚠️ Failed to set webhook! Will retry automatically.\n\nError: ${e.message}\nAttempt: ${this.retryCount + 1}/${this.maxRetries}`
                ).catch(err => log.error('Failed to send webhook error notification', { stack: err.stack }));
            }
            if (this.retryCount < this.maxRetries) {
                await this._scheduleRetry();
                return false;
            } else {
                log.error('Max webhook retry attempts reached!', {
                    maxRetries: this.maxRetries,
                    lastError: e.message
                });
                if (!config.DEBUG) {
                    this.bot.sendMessage(config.LOG_CHANEL_ID,
                        `🚨 CRITICAL: Webhook setup failed after ${this.maxRetries} attempts!\n\nLast error: ${e.message}\n\nBot may not receive updates!`
                    ).catch(err => log.error('Failed to send critical webhook notification', { stack: err.stack }));
                }

                this.isRetrying = false;
                return false;
            }
        }
    }

    
    async _scheduleRetry() {
        this.retryCount++;
        this.isRetrying = true;
        const delay = Math.min(
            this.baseDelay * Math.pow(2, this.retryCount - 1),
            this.maxDelay
        );

        log.info(`Scheduling webhook retry in ${delay}ms (attempt ${this.retryCount + 1}/${this.maxRetries})`);
        if (this.retryTimer) {
            clearTimeout(this.retryTimer);
        }
        this.retryTimer = setTimeout(async () => {
            log.info(`Retrying webhook setup (attempt ${this.retryCount + 1}/${this.maxRetries})...`);
            await this._attemptWebhookSetup();
        }, delay);
    }

    
    async monitorWebhookHealth() {
        if (this.isRetrying) {
            log.debug('Webhook retry already in progress, skipping health check');
            return;
        }

        try {
            const webhookInfo = await this.bot.getWebHookInfo();
            if (!webhookInfo.url || webhookInfo.url !== this.webhookUrl) {
                log.warn('Webhook URL mismatch detected!', {
                    expected: this.webhookUrl,
                    actual: webhookInfo.url
                });
                this.retryCount = 0;
                await this._attemptWebhookSetup();
                return;
            }
            if (webhookInfo.last_error_date) {
                const errorDate = new Date(webhookInfo.last_error_date * 1000);
                const timeSinceError = Date.now() - errorDate.getTime();
                if (timeSinceError < 5 * 60 * 1000) {
                    log.warn('Recent webhook error detected', {
                        last_error_date: errorDate.toISOString(),
                        last_error_message: webhookInfo.last_error_message,
                        pending_update_count: webhookInfo.pending_update_count
                    });
                    if (webhookInfo.pending_update_count > 50) {
                        if (!config.DEBUG) {
                            this.bot.sendMessage(config.LOG_CHANEL_ID,
                                `⚠️ Webhook has ${webhookInfo.pending_update_count} pending updates!\n\nLast error: ${webhookInfo.last_error_message}`
                            ).catch(e => log.error('Failed to send webhook health notification', { stack: e.stack }));
                        }
                    }
                }
            }

            log.debug('Webhook health check passed', {
                url: webhookInfo.url,
                pending_updates: webhookInfo.pending_update_count
            });

        } catch (e) {
            log.error('Error during webhook health monitoring', { stack: e.stack });
        }
    }

    
    cancelRetry() {
        if (this.retryTimer) {
            clearTimeout(this.retryTimer);
            this.retryTimer = null;
        }
        this.isRetrying = false;
        this.retryCount = 0;
    }

    
    getStatus() {
        return {
            isRetrying: this.isRetrying,
            retryCount: this.retryCount,
            maxRetries: this.maxRetries,
            lastSuccessfulSetup: this.lastSuccessfulSetup,
            webhookUrl: this.webhookUrl
        };
    }
}

export default WebhookRetryManager;
