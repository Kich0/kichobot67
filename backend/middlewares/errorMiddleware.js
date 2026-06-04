import ApiError from "../exceptions/apiError.js";

const errorMiddleware = (err, req, res, next) => {
    if (err instanceof ApiError){
        return res.status(err.status).json({errors:err.errors, message:err.message})
    }
    return res.status(500).json({message:"Непредвиденная ошибочка. Сори( ", error:err.stack})
}

export default errorMiddleware
