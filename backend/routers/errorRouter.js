import {Router} from "express";

export const errorRouter = new Router()

errorRouter.get("/:code", (req,res) => res.status(req.params.code).json(req.params.code))