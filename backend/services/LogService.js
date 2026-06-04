import Log from "../models/Log.js";

class LogService {
    async getLogsCount() {
        return Log.countDocuments()
    }

    async getLogs(from, count){
        return Log.find().sort({ timestamp: -1}).skip(from).limit(count);
    }
}

export default new LogService()