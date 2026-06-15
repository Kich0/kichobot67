import express, {Router} from "express"
import {authRouter} from "./routers/authRouter.js";
import {logsRouter} from "./routers/logsRouter.js";
import {scheduleRouter} from "./routers/scheduleRouter.js";
import {errorRouter} from "./routers/errorRouter.js";
import {teacherRouter} from "./routers/teacherRouter.js";
import {teacherScheduleRouter} from "./routers/teacherScheduleRouter.js";
import {groupRouter} from "./routers/groupRouter.js";
import {usersRouter} from "./routers/usersRouter.js";
import {browserRouter} from "./routers/browserRouter.js";
import {gptAssistantRouter} from "./routers/gptAssistantRouter.js";

const router = new Router()

router.use('/gpt-input-pictures', express.static('gpt-input-pictures'))
router.use('/static', express.static('static'))
router.use("/auth", authRouter)
router.use("/logs", logsRouter)
router.use("/schedule", scheduleRouter)
router.use("/error", errorRouter)
router.use("/teacher", teacherRouter)
router.use("/teacherSchedule", teacherScheduleRouter)
router.use("/groups", groupRouter)
router.use("/users", usersRouter)
router.use("/browser", browserRouter)
router.use('/gpt', gptAssistantRouter)

export default router