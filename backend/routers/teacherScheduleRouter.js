import {Router} from "express";
import TeacherScheduleController from "../controllers/TeacherScheduleController.js";

export const teacherScheduleRouter = new Router()

teacherScheduleRouter.get("/get_departments_list", TeacherScheduleController.get_departments_list)
teacherScheduleRouter.get("/get_teachers_list/:id", TeacherScheduleController.get_teachers_list)
teacherScheduleRouter.get("/get_teacher_schedule/:id", TeacherScheduleController.get_teacher_schedule)