import {Router} from "express";
import BrowserController from "../controllers/BrowserController.js";
import TeacherScheduleController from "../controllers/TeacherScheduleController.js";

export const teacherScheduleRouter = new Router()

teacherScheduleRouter.get("/get_departments_list", BrowserController.allChecksCall, TeacherScheduleController.get_departments_list)
teacherScheduleRouter.get("/get_teachers_list/:id", BrowserController.allChecksCall, TeacherScheduleController.get_teachers_list)
teacherScheduleRouter.get("/get_teacher_schedule/:id", BrowserController.allChecksCall, TeacherScheduleController.get_teacher_schedule)