import log from "../../../logging/logging.js";
import { bot } from "../../../app.js";
import userService from "../../../services/userService.js";
import facultyService from "../../../services/facultyService.js";
import programService from "../../../services/programService.js";
import departmentService from "../../../services/departmentService.js";
import groupService from "../../../services/groupService.js";
import teacherService from "../../../services/teacherService.js";
import BackendScheduleService from "../../../../backend/services/ScheduleService.js";
import BackendTeacherScheduleService from "../../../../backend/services/TeacherScheduleService.js";
import { searchGroupMenuCache } from "../searchGroupCommandController.js";
import { searchTeacherMenuCache } from "../searchTeacherCommandController.js";

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

export async function syncNewDataController(msg) {
    try {
        if (!await userService.isAdmin(msg.from.id)) {
            return await bot.sendMessage(msg.chat.id, "⛔ У вас нет прав администратора для этой команды.");
        }

        const startTime = Date.now();
        let statusMsg = await bot.sendMessage(
            msg.chat.id,
            "🔄 <b>Запуск безопасной синхронизации...</b>\n\n" +
            "Проверяю сайт КарУ на наличие новых групп, преподавателей, кафедр и программ без дублирования данных.",
            { parse_mode: "HTML" }
        );

        let lastStatusUpdate = 0;
        const updateStatus = async (text) => {
            const now = Date.now();
            if (now - lastStatusUpdate < 2000) return;
            lastStatusUpdate = now;
            try {
                await bot.editMessageText(text, {
                    chat_id: statusMsg.chat.id,
                    message_id: statusMsg.message_id,
                    parse_mode: "HTML"
                });
            } catch (ignore) {}
        };

        // ==========================================
        // 1. ФАКУЛЬТЕТЫ И ПРОГРАММЫ
        // ==========================================
        await updateStatus("⏳ <b>[1/4] Проверка факультетов и программ...</b>");
        log.info("[Sync] Шаг 1/4: Получение факультетов и программ...");

        const rawFaculties = await BackendScheduleService.get_faculty_list().catch(e => {
            log.error("[Sync] Ошибка при получении факультетов: " + e.message);
            return [];
        });

        // Дедупликация факультетов
        const uniqueFaculties = [];
        const seenFacIds = new Set();
        for (const f of rawFaculties) {
            if (f && f.id && !seenFacIds.has(f.id)) {
                seenFacIds.add(f.id);
                uniqueFaculties.push(f);
            }
        }

        const existingFaculties = await facultyService.getAll();
        const existingFacIds = new Set(existingFaculties.map(f => f.id));
        const newFaculties = uniqueFaculties.filter(f => !existingFacIds.has(f.id));

        if (uniqueFaculties.length > 0) {
            await facultyService.updateAll(uniqueFaculties);
        }

        // Программы для каждого факультета
        const allPrograms = [];
        for (const faculty of uniqueFaculties) {
            try {
                const progList = await BackendScheduleService.get_programs_by_faculty_name(faculty.name, faculty.id);
                for (const p of progList) {
                    allPrograms.push({
                        name: p.name,
                        id: p.id,
                        href: p.href,
                        faculty: p.facultyId !== undefined ? p.facultyId : faculty.id,
                    });
                }
            } catch (e) {
                log.warn(`[Sync] Ошибка получения программ для факультета ${faculty.name}: ` + e.message);
            }
            await sleep(150);
        }

        // Дедупликация программ
        const uniquePrograms = [];
        const seenProgIds = new Set();
        for (const p of allPrograms) {
            if (p && p.id && !seenProgIds.has(p.id)) {
                seenProgIds.add(p.id);
                uniquePrograms.push(p);
            }
        }

        const existingPrograms = await programService.getAll();
        const existingProgIds = new Set(existingPrograms.map(p => p.id));
        const newPrograms = uniquePrograms.filter(p => !existingProgIds.has(p.id));

        if (uniquePrograms.length > 0) {
            await programService.updateAll(uniquePrograms);
        }

        // ==========================================
        // 2. КАФЕДРЫ
        // ==========================================
        await updateStatus("⏳ <b>[2/4] Проверка кафедр...</b>");
        log.info("[Sync] Шаг 2/4: Получение кафедр...");

        const rawDepartments = await BackendTeacherScheduleService.get_departments_list().catch(e => {
            log.error("[Sync] Ошибка при получении кафедр: " + e.message);
            return [];
        });

        const uniqueDepartments = [];
        const seenDeptIds = new Set();
        for (const d of rawDepartments) {
            if (d && d.id && !seenDeptIds.has(d.id)) {
                seenDeptIds.add(d.id);
                uniqueDepartments.push(d);
            }
        }

        const existingDepartments = await departmentService.getAll();
        const existingDeptIds = new Set(existingDepartments.map(d => d.id));
        const newDepartments = uniqueDepartments.filter(d => !existingDeptIds.has(d.id));

        if (uniqueDepartments.length > 0) {
            await departmentService.updateAll(uniqueDepartments);
        }

        // ==========================================
        // 3. СТУДЕНЧЕСКИЕ ГРУППЫ
        // ==========================================
        await updateStatus("⏳ <b>[3/4] Быстрый поиск и сбор групп...</b>");
        log.info("[Sync] Шаг 3/4: Сбор групп по программам...");

        const rawGroups = await BackendScheduleService.get_all_groups_fast(uniquePrograms, (stage, count) => {
            if (stage % 20 === 0) {
                updateStatus(`⏳ <b>[3/4] Сбор групп: ${stage}%</b> (${count} найдено)`);
            }
        }).catch(e => {
            log.error("[Sync] Ошибка при сборе групп: " + e.message);
            return [];
        });

        const uniqueGroups = [];
        const seenGroupIds = new Set();
        for (const g of rawGroups) {
            if (g && g.id && !seenGroupIds.has(g.id)) {
                seenGroupIds.add(g.id);
                uniqueGroups.push(g);
            }
        }

        const existingGroups = await groupService.getAll();
        const existingGroupIds = new Set(existingGroups.map(g => g.id));
        const newGroups = uniqueGroups.filter(g => !existingGroupIds.has(g.id));

        if (uniqueGroups.length > 0) {
            // Атомарный upsert без удаления старых групп (removeStale = false)
            await groupService.updateAll(uniqueGroups, false);
            if (newGroups.length > 0 && searchGroupMenuCache) {
                for (const key of Object.keys(searchGroupMenuCache)) {
                    delete searchGroupMenuCache[key];
                }
            }
        }

        // ==========================================
        // 4. ПРЕПОДАВАТЕЛИ
        // ==========================================
        await updateStatus("⏳ <b>[4/4] Сбор преподавателей по кафедрам...</b>");
        log.info("[Sync] Шаг 4/4: Сбор преподавателей...");

        const allTeachers = [];
        let deptIndex = 0;
        for (const dept of uniqueDepartments) {
            deptIndex++;
            try {
                const teachersList = await BackendTeacherScheduleService.get_teachers_list(dept.id);
                if (Array.isArray(teachersList)) {
                    for (const t of teachersList) {
                        allTeachers.push({
                            name: t.name,
                            id: t.id,
                            href: t.href,
                            department: t.departmentId || dept.id,
                        });
                    }
                }
            } catch (e) {
                log.warn(`[Sync] Ошибка получения преподавателей кафедры ${dept.name}: ` + e.message);
            }

            if (deptIndex % 10 === 0 || deptIndex === uniqueDepartments.length) {
                const pct = Math.floor((deptIndex / uniqueDepartments.length) * 100);
                updateStatus(`⏳ <b>[4/4] Сбор преподавателей: ${pct}%</b> (кафедра ${deptIndex}/${uniqueDepartments.length})`);
            }
            await sleep(200);
        }

        // Дедупликация преподавателей по ID в памяти (исключает профессоров с нескольких кафедр)
        const uniqueTeachers = [];
        const seenTeacherIds = new Set();
        for (const t of allTeachers) {
            if (t && t.id && !seenTeacherIds.has(t.id)) {
                seenTeacherIds.add(t.id);
                uniqueTeachers.push(t);
            }
        }

        const existingTeachers = await teacherService.getAll();
        const existingTeacherIds = new Set(existingTeachers.map(t => t.id));
        const newTeachers = uniqueTeachers.filter(t => !existingTeacherIds.has(t.id));

        if (uniqueTeachers.length > 0) {
            await teacherService.updateAll(uniqueTeachers);
            if (newTeachers.length > 0 && searchTeacherMenuCache) {
                for (const key of Object.keys(searchTeacherMenuCache)) {
                    delete searchTeacherMenuCache[key];
                }
            }
        }

        // ==========================================
        // 5. ОЧИСТКА ДУБЛИКАТОВ ИЗ БД (САНИТАРИЯ)
        // ==========================================
        const cleanedTeacherDups = await teacherService.deduplicateTeachers().catch(() => 0);
        const cleanedGroupDups = await groupService.deduplicateGroups().catch(() => 0);

        const totalElapsed = Math.floor((Date.now() - startTime) / 1000);
        log.info(`[Sync] Синхронизация завершена за ${totalElapsed} сек. Новых: групп +${newGroups.length}, преподов +${newTeachers.length}, кафедр +${newDepartments.length}, программ +${newPrograms.length}`);

        // ==========================================
        // 6. ФОРМИРОВАНИЕ ИТОГОВОГО ОТЧЕТА
        // ==========================================
        let report = `✅ <b>Синхронизация успешно завершена!</b>\n`;
        report += `⏱ <i>Время выполнения: ${totalElapsed} сек.</i>\n\n`;

        report += `📊 <b>Результаты сканирования КарУ:</b>\n`;
        report += `• 🏛 Факультеты: <b>${uniqueFaculties.length}</b> ${newFaculties.length > 0 ? `(новых: <b>+${newFaculties.length}</b>)` : '✓'}\n`;
        report += `• 📑 Программы: <b>${uniquePrograms.length}</b> ${newPrograms.length > 0 ? `(новых: <b>+${newPrograms.length}</b>)` : '✓'}\n`;
        report += `• 🏢 Кафедры: <b>${uniqueDepartments.length}</b> ${newDepartments.length > 0 ? `(новых: <b>+${newDepartments.length}</b>)` : '✓'}\n`;
        report += `• 👥 Группы: <b>${uniqueGroups.length}</b> ${newGroups.length > 0 ? `(новых: <b>+${newGroups.length}</b>)` : '✓'}\n`;
        report += `• 👨‍🏫 Преподаватели: <b>${uniqueTeachers.length}</b> ${newTeachers.length > 0 ? `(новых: <b>+${newTeachers.length}</b>)` : '✓'}\n\n`;

        report += `🛡 <b>Защита от дубликатов:</b>\n`;
        report += `• Метод: атомарный <code>upsert</code> по уникальным ID\n`;
        if (cleanedTeacherDups > 0 || cleanedGroupDups > 0) {
            report += `• Удалено ранее существовавших дублей: преподавателей — ${cleanedTeacherDups}, групп — ${cleanedGroupDups}\n`;
        } else {
            report += `• Старых дубликатов в базе не обнаружено (чисто ✓)\n`;
        }
        report += `\n`;

        const hasNewData = newGroups.length > 0 || newTeachers.length > 0 || newDepartments.length > 0 || newPrograms.length > 0;

        if (hasNewData) {
            if (newGroups.length > 0) {
                const sampleGroups = newGroups.slice(0, 10).map(g => g.name).join(', ');
                report += `🆕 <b>Новые группы (+${newGroups.length}):</b>\n<code>${sampleGroups}</code>${newGroups.length > 10 ? ` <i>...и ещё ${newGroups.length - 10}</i>` : ''}\n\n`;
            }
            if (newTeachers.length > 0) {
                const sampleTeachers = newTeachers.slice(0, 10).map(t => t.name).join(', ');
                report += `🆕 <b>Новые преподаватели (+${newTeachers.length}):</b>\n<code>${sampleTeachers}</code>${newTeachers.length > 10 ? ` <i>...и ещё ${newTeachers.length - 10}</i>` : ''}\n\n`;
            }
            if (newDepartments.length > 0) {
                const sampleDepts = newDepartments.slice(0, 5).map(d => d.name).join(', ');
                report += `🆕 <b>Новые кафедры (+${newDepartments.length}):</b>\n<code>${sampleDepts}</code>\n\n`;
            }
        } else {
            report += `ℹ️ <i>Все данные полностью актуальны. Новых групп или преподавателей на сайте КарУ не обнаружено.</i>`;
        }

        await bot.editMessageText(report, {
            chat_id: statusMsg.chat.id,
            message_id: statusMsg.message_id,
            parse_mode: "HTML"
        });

    } catch (e) {
        log.error("[Sync] Критическая ошибка при синхронизации: " + e.message, { stack: e.stack });
        try {
            await bot.sendMessage(msg.chat.id, `❌ <b>Ошибка при синхронизации:</b>\n<code>${e.message}</code>`, { parse_mode: "HTML" });
        } catch (ignore) {}
    }
}
