import jwt from "jsonwebtoken";
import config from "../config.js";
import ApiError from "../exceptions/apiError.js";

const roleMiddleware = (roles) => {
    return function (req, res, next) {
        if (req.method === "OPTIONS") {
            next()
        }

        try {
            if (!req.headers.authorization) {
                return next(ApiError.UnauthorizedError())
            } else {
                const token = req.headers.authorization.split(' ')[1]
                if (!token) {
                    return next(ApiError.UnauthorizedError())
                }

                const {roles: userRoles} = jwt.verify(token, config.JWT_ACCESS_SECRET)
                let hasRole = false
                userRoles.forEach(role => {
                    if (roles.includes(role)) {
                        hasRole = true
                    }
                })
                if (!hasRole) {
                    return next(ApiError.Forbidden())
                }
                next();
            }
        } catch (e) {
            next(ApiError.UnauthorizedError())
        }
    }
};

export default roleMiddleware