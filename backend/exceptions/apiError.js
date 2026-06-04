class ApiError extends Error {
    status;
    errors;

    constructor(status, message, errors = []) {
        super(message);
        this.status = status
        this.errors = errors
    }

    static BadRequest(message, errors = []) {
        return new ApiError(400, message, errors)
    }

    static UnauthorizedError(message = "Пользователь не авторизован", errors = []) {
        return new ApiError(401, message, errors)
    }

    static Forbidden(message = "У вас нет прав доступа к данной команде", errors = []) {
        return new ApiError(403, message, errors)
    }

    static Not_Found(message = "Ресурс не найден. ", errors = []) {
        return new ApiError(404, message, errors)
    }

    static Conflict(message, errors = []) {
        return new ApiError(409, message, errors)
    }

    static ServiceUnavailable(message, errors = []) {
        return new ApiError(503, message, errors)
    }

}

export default ApiError