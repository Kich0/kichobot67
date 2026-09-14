import {Router} from "express";
import {scheduleRouter} from "./routers/scheduleRouter.js";
import {teacherRouter} from "./routers/teacherRouter.js";
import {teacherScheduleRouter} from "./routers/teacherScheduleRouter.js";
import {browserRouter} from "./routers/browserRouter.js";

const router = new Router();

router.use("/schedule", scheduleRouter);
router.use("/teacher", teacherRouter);
router.use("/teacherSchedule", teacherScheduleRouter);
router.use("/browser", browserRouter);

export default router;