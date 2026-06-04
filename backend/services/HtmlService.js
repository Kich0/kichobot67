import cheerio from "cheerio";

class HtmlService {
    htmlTableToJson(tableHTML) {
        const $ = cheerio.load(tableHTML);
        const tableData = [];

        $('table tr').each((index, row) => {
            const rowData = [];
            $(row).find('th, td').each((index, cell) => {
                rowData.push($(cell).html());
            });
            tableData.push(rowData);
        });
        return tableData
    }
}

export default new HtmlService()