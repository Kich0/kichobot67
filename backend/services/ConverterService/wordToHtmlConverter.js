import { spawn } from "child_process";
/*
* wordToHtmlByLibreOffice
* @param docName - путь к файлу doc
* @param outdir - путь к директории, куда сохранить docx
* @param args - дополнительные аргументы для libreoffice
* @returns Promise<void>
* */
export async function docToDocxByLibreOffice(docName, outdir, args = []) {
    return new Promise((resolve, reject) => {
        const commandPrompt = ['--headless', '--convert-to', 'docx', docName, '--outdir', outdir, ...args];
        let libreoffice = spawn("libreoffice", commandPrompt);
        libreoffice.stdout.on("data", (data) => {
            console.log('stdout:', data.toString());
        });
        libreoffice.on("error", (err) => {
            console.error(`Ошибка конвертации файла ${docName}. ` + err.stack);
            reject(err);
        });
        libreoffice.on("exit", (code, signal) => {
            if (code !== 0) {
                console.error(`Ошибка конвертации файла ${docName}. Код: ${code} ${signal}`);
                reject(new Error('Ошибка конвертации файла. Код: ' + code + ' ' + signal));
            }
            else {
                console.log(`Конвертация файла ${docName} завершена успешно`);
                resolve();
            }
        });
    });
}
/*
* wordToHtmlByPandoc
* @param wordName - путь к файлу word
* @param htmlName - путь к файлу html
* @param args - дополнительные аргументы для pandoc
* @returns Promise<void>
* */
export async function wordToHtmlByPandoc(wordName, htmlName, args = []) {
    return new Promise((resolve, reject) => {
        const commandPrompt = [wordName, '-o', htmlName, '--self-contained', ...args];
        let pandoc = spawn("pandoc", commandPrompt);
        pandoc.stdout.on("data", (data) => {
            console.log('stdout:', data.toString());
        });
        pandoc.on("error", (err) => {
            console.error(`Ошибка конвертации файла ${wordName}. ` + err.stack);
            reject(err);
        });
        pandoc.on("exit", (code, signal) => {
            if (code !== 0) {
                console.error(`Ошибка конвертации файла ${wordName}. Код: ${code} ${signal}`);
                reject(new Error('Ошибка конвертации файла. Код: ' + code + ' ' + signal));
            }
            else {
                console.log(`Конвертация файла ${wordName} завершена успешно`);
                resolve();
            }
        });
    });
}
