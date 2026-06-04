module.exports = {
    apps: [
        {
            name: "kichobot-api",
            script: "npm",
            args: "run start",
            log_date_format: "YYYY-MM-DD HH:mm:ss",
            autorestart: true,
        }
    ]
}
