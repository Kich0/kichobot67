import { validationResult } from "express-validator";
import UserService from "../services/UserService.js";
import ApiError from "../exceptions/apiError.js";
import userService from "../services/UserService.js";
import log from "../logging/logging.js";
import { UserDTO } from "../dtos/UserDTO.js";
import GroupService from "../services/GroupService.js";
import { GroupDTO } from "../dtos/GroupDTO.js";

class UserController {
  async registration(req, res, next) {
    try {
      const errors = validationResult(req);
      if (!errors.isEmpty()) {
        return next(
          ApiError.BadRequest("Ошибка при валидации данных.", errors.array())
        );
      }

      const { username, password } = req.body;
      const userData = await UserService.registration(username, password);
      res.cookie("refreshToken", userData.refreshToken, {
        maxAge: 30 * 24 * 60 * 60 * 1000,
        httpOnly: true,
      });
      return res.json(userData);
    } catch (e) {
      log.error("Ошибка при регистрации пользователя: ", e);
      next(e);
    }
  }

  async login(req, res, next) {
    try {
      const { username, password } = req.body;
      const userData = await userService.login(username, password);
      res.cookie("refreshToken", userData.refreshToken, {
        maxAge: 30 * 24 * 60 * 60 * 1000,
        httpOnly: true,
      });
      res.cookie("accessToken", userData.accessToken, {
        maxAge: 15 * 60 * 1000,
        httpOnly: true,
      });
      return res.json(userData);
    } catch (e) {
      log.error("Ошибка при авторизации пользователя: ", e);
      next(e);
    }
  }

  async logout(req, res, next) {
    try {
      const { refreshToken } = req.cookies;
      await UserService.logout(refreshToken);
      res.clearCookie("refreshToken");
      res.clearCookie("accessToken");
      return res.status(200).json("Вы успешно вышли из аккаунта!");
    } catch (e) {
      log.error("Ошибка при логауте пользователя: ", e);
      next(e);
    }
  }

  async refresh(req, res, next) {
    try {
      const { refreshToken } = req.cookies;
      const userData = await UserService.refresh(refreshToken);
      res.cookie("refreshToken", userData.refreshToken, {
        maxAge: 30 * 24 * 60 * 60 * 1000,
        httpOnly: true,
      });
      return res.json(userData);
    } catch (e) {
      log.error("Ошибка при рефреше токена доступа пользователя: ", e);
      next(e);
    }
  }

  async isAdmin(req, res, next) {
    try {
      res.json("Вы админ!");
    } catch (e) {
      log.error("Ошибка при проверке пользователя на админа: ", e);
      next(e);
    }
  }

  async get_me(req, res, next) {
    try {
      const userId = req.user.userId;
      const user = await UserService.get_one(userId);

      const userDto = new UserDTO(user);

      return res.json(userDto);
    } catch (e) {
      next(e);
    }
  }

  async my_groups(req, res, next) {
    try {
      const userId = req.user.userId;
      const groups = await GroupService.get_all_by_userId(userId);

      return res.json(groups.map((group) => new GroupDTO(group)));
    } catch (e) {
      next(e);
    }
  }
}

export default new UserController();
