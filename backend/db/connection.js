import mongoose from "mongoose"


class Database{
    async connect(URI){
        try {
            // Подключение к MongoDB с использованием URL базы данных
            await mongoose.connect(URI, {
                useNewUrlParser: true,
                useUnifiedTopology: true,
            });

            // Если существует отдельный экземпляр mongoose в bot/node_modules (локальный dev),
            // подключаем и его, чтобы не было timeout буферизации
            try {
                const botMongooseModule = await import("../../bot/node_modules/mongoose/index.js").catch(() => null);
                const botMongoose = botMongooseModule?.default;
                if (botMongoose && botMongoose !== mongoose && botMongoose.connection.readyState === 0) {
                    await botMongoose.connect(URI, {
                        useNewUrlParser: true,
                        useUnifiedTopology: true,
                    });
                }
            } catch (ignore) {}
        } catch (error) {
            throw new Error(error.message);
        }
    };
}


export default new Database()

