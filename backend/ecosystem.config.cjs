module.exports = {
    apps: [
        {
            name: "tolyan-express-js",
            script: "npm",
            args: "run start",
            log_date_format: "YYYY-MM-DD HH:mm:ss",
            autorestart: true,
        }
    ]
}
