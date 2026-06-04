import jwt from "jsonwebtoken";
import Token from "../models/Token.js";
import config from "../config.js"

class TokenService {
    generateTokens(payload) {
        const accessToken = jwt.sign(payload, config.JWT_ACCESS_SECRET, {expiresIn: "30m"})
        const refreshToken = jwt.sign(payload, config.JWT_REFRESH_SECRET, {expiresIn: "30d"})
        return {
            accessToken,
            refreshToken
        }
    }

    validateRefreshToken(token) {
        try {
            return jwt.verify(token, config.JWT_REFRESH_SECRET)
        } catch (e) {
            return null
        }
    }

    async saveToken(userId, refreshToken) {
        const tokenData = await Token.findOne({user: userId})
        if (tokenData) {
            tokenData.refreshToken = refreshToken
            return tokenData.save()
        }
        return await Token.create({user: userId, refreshToken})
    }

    async removeToken(refreshToken) {
        return Token.deleteOne({refreshToken});
    }

    async findToken(refreshToken) {
        return Token.findOne({refreshToken});
    }

}

export default new TokenService()