import {Router} from "express";
import authMiddleware from "../middlewares/authMiddleware.js";
import GroupController from "../controllers/GroupController.js";
import {body} from "express-validator"
// import roleMiddleware from "../middlewares/roleMiddleware.js";

// const customRegex = /^[a-zA-Z0-9_\-\.]+$/;

export const groupRouter = new Router()


groupRouter.post("",
    authMiddleware,
    body("name", "Поле name не должно быть пустым").notEmpty(),
    GroupController.create)


groupRouter.get("/:group_id",
    authMiddleware,
    GroupController.get_one)

groupRouter.delete("/:group_id",
    authMiddleware,
    GroupController.delete)

groupRouter.get("/:group_id/preview",
    authMiddleware,
    GroupController.get_one_mini)

groupRouter.post("/:group_id/join",
    authMiddleware,
    GroupController.send_join_request)

groupRouter.post("/:group_id/accept_join_request",
    authMiddleware,
    body("requestAuthorId", "Поле requestAuthorId не должно быть пустым").notEmpty(),
    GroupController.accept_join_request)