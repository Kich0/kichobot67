import jwt from "jsonwebtoken";
import config from "../config.js";
import ApiError from "../exceptions/apiError.js";

const authMiddleware = (req, res, next) => {
  if (req.method === "OPTIONS") {
    next();
  }
  try {
    if (req.headers.cookie) {
      const matches = req.headers.cookie.match(
        /refreshToken=([^;]+).*?accessToken=([^;]+)|accessToken=([^;]+).*?refreshToken=([^;]+)/
      );
      const accessToken = matches[2] || matches[3];

      if (!accessToken) {
        return next(ApiError.UnauthorizedError());
      }
      req.user = jwt.verify(accessToken, config.JWT_ACCESS_SECRET);
      next();
    } else {
      return next(ApiError.UnauthorizedError());
    }
  } catch (e) {
    next(ApiError.UnauthorizedError());
  }
};

export default authMiddleware;
