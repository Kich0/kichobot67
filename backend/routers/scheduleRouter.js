import {Router} from "express";
import ScheduleController from "../controllers/ScheduleController.js"
import BrowserController from "../controllers/BrowserController.js";

export const scheduleRouter = new Router()

scheduleRouter.get("/get_faculty_list", BrowserController.allChecksCall, ScheduleController.get_faculty_list)
scheduleRouter.get("/get_program_list_by_facultyId/:id", BrowserController.allChecksCall, ScheduleController.get_program_list_by_facultyId)
scheduleRouter.get("/get_group_list_by_programId/:id", BrowserController.allChecksCall, ScheduleController.get_group_list_by_programId)
scheduleRouter.get("/get_schedule_by_groupId/:id/:language", BrowserController.allChecksCall, ScheduleController.get_schedule_by_groupId)
scheduleRouter.get("/get_all_data", BrowserController.allChecksCall, ScheduleController.get_all_data)
scheduleRouter.get("/restart_browser", BrowserController.restartBrowser)