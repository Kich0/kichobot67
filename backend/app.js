import express from "express";
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
    limit: '50mb'
}));
app.use(cookieParser());
app.use(bodyParser.text({type: 'text/html', limit: '50mb'}));
app.use((req, res, next) => {
    const decodedUrl = decodeURIComponent(req.url);
    log.info(`${req.method} ${decodedUrl}`);
    next();
});
app.use('/express/api', router);
app.use(errorMiddleware);

const appStart = async () => {
    try {
        await db.connect(config.DB_URI);
        app.listen(config.PORT);
    } catch (e) {
        log.error("Ошибка при запуске ksu-helper" + e);
    }
    await setupLoggingPathUpdate();
    await setupKsuReAuth();
};

appStart().then(() =>
    log.info(`App has been ran! http://localhost:${config.PORT}`)
).catch(e => console.log("Ошибка при запуске express приложения: " + e.stack))
