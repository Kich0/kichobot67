import {Router} from "express";
// import roleMiddleware from "../middlewares/roleMiddleware.js";
import log from "../logging/logging.js";
import LogService from "../services/LogService.js";

export const logsRouter = new Router()

logsRouter.post('/add_log', async (req, res, next) => {
    try {
        log.info("Добавлен лог из апи!")
        return res.status(200).json("Добавлен лог в бд!!!")
    } catch (e) {
        next(e)
    }
})

logsRouter.post('/add_log2', async (req, res, next) => {
    try {
        log.info("That is new log from database. A very very long log, almost like my .... Khe. How are u? Not too long?")
        return res.status(200).json("Добавлен long лог в бд!!!")
    } catch (e) {
        next(e)
    }
})

logsRouter.post('/add_warn_log', async (req, res, next) => {
    try {
        log.warn("test warn log")
        return res.status(200).json("Добавлен warn лог в бд!!!")
    } catch (e) {
        next(e)
    }
})

logsRouter.post('/add_error_log', async (req, res, next) => {
    try {
        log.error("test error log")
        return res.status(200).json("Добавлен error лог в бд!!!")
    } catch (e) {
        next(e)
    }
})

logsRouter.get("/get_logs", async (req,res, next) => {
    const page = parseInt(req.query.page) || 1;
    const limit = parseInt(req.query.limit) || 50;
    const skip = (page - 1) * limit;

    try {
        const totalDocuments = await LogService.getLogsCount()
        const documents = await LogService.getLogs(skip,limit)

        res.json({
            page,
            limit,
            totalPages: Math.ceil(totalDocuments / limit),
            documents,
        });
    } catch (error) {
        next(error)
    }

})
