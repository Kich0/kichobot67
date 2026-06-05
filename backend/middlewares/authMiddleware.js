import jwt from "jsonwebtoken";
import config from "../config.js";
import ApiError from "../exceptions/apiError.js";

const authMiddleware = (req, res, next) => {
  if (req.method === "OPTIONS") {
    return next();
  }
  try {
    const accessToken = req.cookies?.accessToken;

    if (!accessToken) {
      return next(ApiError.UnauthorizedError());
    }
    req.user = jwt.verify(accessToken, config.JWT_ACCESS_SECRET);
    next();
  } catch (e) {
    next(ApiError.UnauthorizedError());
  }
};

export default authMiddleware;
