import fs from "fs";
import multer from "multer";
import * as uuid from "uuid";
import queue from 'async/queue.js';
import {docToDocxByLibreOffice, wordToHtmlByPandoc} from "../services/ConverterService/wordToHtmlConverter.js";


const q = queue(async (taskData) => {
    const callback = taskData.callback;
    const file = taskData.file;
    try {
        await docToDocxByLibreOffice(file.path, 'uploads/temp/');
        callback(null, `Успешно сконвертирован.`);
    } catch (error) {
        callback(error, null);
    }
}, 1); // Указываем максимальное количество одновременных операций равным 1


const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        const uploadFolder = 'uploads/';
        if (!fs.existsSync(uploadFolder)) {
            fs.mkdirSync(uploadFolder);
            fs.mkdirSync(uploadFolder + 'converted');
            fs.mkdirSync(uploadFolder + 'temp');
        }
        cb(null, uploadFolder)
    },
    filename: (req, file, cb) => {
        const uniqueSuffix = Date.now() + '-' + Math.round(Math.random() * 1E9);
        cb(null, `${uuid.v4()}-${uniqueSuffix}-${file.originalname}`);
    }
})

export function fileCheckMiddleware(req, res, next) {
    if (!req.file) {
        return res.status(400).json({error: 'Вы не загрузили файл'});
    }
    next();
}

export const upload = multer({
    storage: storage,
    limits: { fileSize: 20 * 1024 * 1024 } // 20 MB limit to prevent DoS
})

export async function wordToHtml(req, res, next) {
    try {
        const fileExtension = '.' + req.file.originalname.split('.').pop();

        if (fileExtension === '.doc') {
            convertToDocxAndToHtmlByPandoc(req, res, fileExtension)
        } else if (fileExtension === '.docx') {
            const htmlPath = req.file.path.replace('uploads', 'uploads/converted').replace(fileExtension, '.html');
            const htmlAbsolutePath = `${process.cwd()}/${htmlPath}`;

            await wordToHtmlByPandoc(req.file.path, htmlAbsolutePath)
                .then(() => {
                    res.sendFile(htmlAbsolutePath, (err) => {
                        if (err) {
                            console.error('Ошибка отправки файла:', err);
                            throw new Error('Ошибка отправки файла');
                        } else {
                            fs.rmSync(req.file.path) // Удаляем исходный docx файл
                            fs.rmSync(htmlAbsolutePath); // Удаляем полученный html файл
                        }
                    })
                })

        } else {
            return res.status(400).json({error: 'Недопустимый формат файла'});
        }


        function convertToDocxAndToHtmlByPandoc(req, res, fileExtension) {
            q.push({
                file: req.file, callback: async (err, result) => {
                    try{
                        if (err) {
                            console.log(err)
                            return res.status(500).json({error: 'Ошибка конвертации файла'});
                        }

                        const docxPath = req.file.path.replace('uploads', 'uploads/temp').replace(fileExtension, '.docx');

                        const htmlPath = docxPath.replace('uploads/temp', 'uploads/converted').replace(fileExtension, '.html');
                        const htmlAbsolutePath = `${process.cwd()}/${htmlPath}`;

                        await wordToHtmlByPandoc(docxPath, htmlAbsolutePath)
                            .then(() => {
                                res.sendFile(htmlAbsolutePath, (err) => {
                                    if (err) {
                                        console.error('Ошибка отправки файла:', err);
                                        throw new Error('Ошибка отправки файла');
                                    } else {
                                        fs.rmSync(req.file.path) // Удаляем исходный doc файл
                                        fs.rmSync(docxPath) // Удаляем полученный docx файл
                                        fs.rmSync(htmlAbsolutePath); // Удаляем полученный html файл
                                    }
                                })
                            })
                    }catch (e) {
                        console.log(e)
                        return res.status(500).json({error: 'Ошибка конвертации файла doc'});
                    }
                }
            })
        }
    } catch (e) {
        next(e)
    }
}
