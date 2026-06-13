import express from "express";
import dns from "dns";
import cors from "cors";
import cookieParser from "cookie-parser";
import db from "./db/connection.js";
import router from "./router.js";
import config from "./config.js";
import errorMiddleware from "./middlewares/errorMiddleware.js";
import log from "./logging/logging.js";
import {setupLoggingPathUpdate} from "./cron/loggingPathUpdate.js";
import {setupKsuReAuth} from "./cron/ksuReAuth.js";
import bodyParser from "body-parser";

const app = express();

const corsOptions = {
    origin: '*',
    optionsSuccessStatus: 200
}

app.use(cors(corsOptions));
app.use(express.json({
    limit: '5mb'
}));
app.use(cookieParser());
app.use(bodyParser.text({type: 'text/html', limit: '5mb'}));
app.use((req, res, next) => {
    const decodedUrl = decodeURIComponent(req.url);
    log.info(`${req.method} ${decodedUrl}`);
    next();
});
app.use('/express/api', router);
app.get('/', (req, res) => res.send('Backend is alive!'));
app.use(errorMiddleware);

import FreeProxyService from "./services/FreeProxyService.js";

const appStart = async () => {
    try {
        await db.connect(config.DB_URI);
        log.info(`[Database] Успешное подключение к MongoDB`);
        app.listen(config.PORT);
    } catch (e) {
        log.error("Ошибка при запуске kichobot-api: " + e.message);
    }
    await setupLoggingPathUpdate();
    await setupKsuReAuth();
    
    if (config.USE_FREE_PROXIES) {
        FreeProxyService.initPool().catch(e => log.error("[ProxyPool] Ошибка автопополнения: " + e.message));
    }
};

appStart().then(() => {
    log.info(`App has been ran! http://localhost:${config.PORT}`)
    log.info(`USE_FREE_PROXIES Config is: ${config.USE_FREE_PROXIES}`);
    
    // TEST DNS RESOLUTION ON RENDER
    dns.resolve('schedule.buketov.edu.kz', (err, addresses) => {
        if (err) log.error("DNS TEST FAILED: " + err.message);
        else log.info("DNS TEST SUCCESS: " + addresses.join(', '));
    });
}).catch(e => console.log("Ошибка при запуске express приложения: " + e.stack))
