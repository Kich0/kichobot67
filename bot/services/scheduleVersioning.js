import crypto from "crypto";
import log from "../logging/logging.js";
import { ScheduleVersion } from "../models/schedule.js";
import { TeacherScheduleVersion } from "../models/teacherSchedule.js";
import { compareStudentSchedules, compareTeacherSchedules } from "./scheduleDiffService.js";

/**
 * Версионирование расписаний.
 *
 * Основная коллекция (Schedule / TeacherSchedule) хранит ОДИН актуальный «оригинал» на группу/преподавателя
 * (его бот показывает при падении API). Если содержимое расписания изменилось хоть на одну букву —
 * предыдущий оригинал уходит в коллекцию истории (ScheduleVersion / TeacherScheduleVersion), а новый
 * становится актуальным. Если ничего не изменилось — запись только «трогается» (обновляется время проверки).
 *
 * Хранение истории ограничено двумя правилами:
 *  1. Срок: каждая архивная версия автоматически удаляется через 2 месяца после архивации (TTL-индекс, см. модели).
 *  2. Размер: суммарно история (обе коллекции) не превышает 50 МБ — самые старые версии удаляются первыми.
 */

// Суммарный лимит размера истории (обе коллекции вместе)
export const HISTORY_MAX_BYTES = 50 * 1024 * 1024;
// Срок хранения архивных версий — 2 месяца (60 дней). Должен совпадать с `expires` в моделях ScheduleVersion/TeacherScheduleVersion.
export const HISTORY_TTL_SECONDS = 60 * 24 * 60 * 60;

const SIZE_CHECK_INTERVAL_MS = 30 * 1000;
let lastSizeCheck = 0;

const str = (v) => (v === undefined || v === null ? "" : String(v));

/** Проекция расписания группы ровно на те поля, которые хранятся в схеме Schedule. */
export function projectGroupSchedule(data) {
    if (!Array.isArray(data)) return [];
    return data.map((d) => ({
        day: str(d?.day),
        subjects: (Array.isArray(d?.subjects) ? d.subjects : []).map((s) => ({
            time: str(s?.time),
            subject: str(s?.subject),
        })),
    }));
}

/** Проекция расписания преподавателя ровно на те поля, которые хранятся в схеме TeacherSchedule. */
export function projectTeacherSchedule(data) {
    if (!Array.isArray(data)) return [];
    return data.map((d) => ({
        day: str(d?.day),
        groups: (Array.isArray(d?.groups) ? d.groups : []).map((g) => ({
            time: str(g?.time),
            group: str(g?.group),
            subject: str(g?.subject),
            lessonType: str(g?.lessonType),
        })),
    }));
}

/** SHA-256 отпечаток содержимого (меняется при изменении любой буквы). */
export function computeScheduleHash(projected) {
    return crypto.createHash("sha256").update(JSON.stringify(projected)).digest("hex");
}

/**
 * Следит, чтобы суммарный размер истории не превышал maxBytes: если превышает — удаляет самые старые версии
 * (пропорционально размеру каждой коллекции). Проверка не чаще раза в 30 секунд (или при force).
 * @param {Array} models модели коллекций истории (с полем archivedAt)
 * @returns {Promise<{skipped?: boolean, total?: number, deleted?: number}>}
 */
export async function enforceHistorySizeLimit(models, maxBytes = HISTORY_MAX_BYTES, { force = false } = {}) {
    const nowMs = Date.now();
    if (!force && nowMs - lastSizeCheck < SIZE_CHECK_INTERVAL_MS) return { skipped: true };
    lastSizeCheck = nowMs;

    const stats = [];
    for (const M of models) {
        // collStats падает, если коллекция ещё не создана -> считаем её пустой
        const s = await M.db.db.command({ collStats: M.collection.collectionName }).catch(() => null);
        stats.push({ M, size: Number(s?.size) || 0, count: Number(s?.count) || 0 });
    }

    const total = stats.reduce((sum, s) => sum + s.size, 0);
    if (total <= maxBytes) return { total, deleted: 0 };

    let deleted = 0;
    for (const st of stats) {
        if (!st.count || !st.size) continue;
        const targetSize = maxBytes * (st.size / total);
        const excess = st.size - targetSize;
        const avgDoc = st.size / st.count;
        const n = Math.min(st.count, Math.ceil(excess / avgDoc));
        if (n <= 0) continue;
        const oldest = await st.M.find({}).sort({ archivedAt: 1 }).limit(n).select("_id").lean();
        if (oldest.length > 0) {
            await st.M.deleteMany({ _id: { $in: oldest.map((o) => o._id) } });
            deleted += oldest.length;
        }
    }
    return { total, deleted };
}

/** Обёртка для сервисов: проверка лимита 50 МБ после архивации; никогда не бросает исключений. */
export async function enforceScheduleHistoryLimit() {
    try {
        const r = await enforceHistorySizeLimit([ScheduleVersion, TeacherScheduleVersion]);
        if (r.deleted > 0) {
            const mb = (r.total / (1024 * 1024)).toFixed(1);
            log.warn(`[ScheduleHistory] История расписаний была ${mb} МБ (> 50 МБ): удалено ${r.deleted} самых старых версий.`);
        }
    } catch (e) {
        log.error(`[ScheduleHistory] Ошибка контроля размера истории: ${e.message}`);
    }
}

/**
 * Сохраняет расписание с версионированием.
 * @returns {{status: 'unchanged'|'changed'|'created', hash: string}}
 */
export async function saveVersioned({
    Model,
    VersionModel,
    keyField,
    key,
    data,
    project,
    afterArchive,
}) {
    const hash = computeScheduleHash(project(data));
    const now = new Date();

    // 1. Быстрый путь (1 запрос): содержимое идентично актуальному оригиналу -> только обновляем время проверки.
    const touched = await Model.updateOne({ [keyField]: key, hash }, { $set: { hash, checkedAt: now } });
    if (touched && touched.matchedCount > 0) {
        return { status: "unchanged", hash };
    }

    // 2. Атомарная замена оригинала: возвращаем документ «до», чтобы корректно заархивировать старую версию.
    const update = {
        $set: { [keyField]: key, data, hash, changedAt: now, checkedAt: now },
    };
    const options = { upsert: true, returnDocument: "before", lean: true };
    let before;
    try {
        before = await Model.findOneAndUpdate({ [keyField]: key }, update, options);
    } catch (e) {
        // Гонка двух одновременных upsert-ов на unique-индексе — повторяем один раз
        if (e && e.code === 11000) {
            before = await Model.findOneAndUpdate({ [keyField]: key }, update, options);
        } else {
            throw e;
        }
    }

    if (!before) return { status: "created", hash };

    const beforeHash = before.hash || computeScheduleHash(project(before.data));
    if (beforeHash === hash) {
        // Старый документ без хэша (до внедрения версионирования) и содержимое то же самое:
        // ничего не изменилось, возвращаем прежнюю дату изменения.
        await Model.updateOne(
            { [keyField]: key },
            { $set: { changedAt: before.changedAt || before.createdAt || now } }
        );
        return { status: "unchanged", hash };
    }

    // 3. Содержимое изменилось — старый оригинал не удаляем, а сохраняем в историю
    //    (автоудаление через 2 месяца по archivedAt + общий лимит размера 50 МБ).
    let diffResult = null;
    try {
        if (keyField === "groupId") {
            diffResult = compareStudentSchedules(before.data, data);
        } else if (keyField === "teacherId") {
            diffResult = compareTeacherSchedules(before.data, data);
        }
    } catch (e) {
        log.error(`[ScheduleHistory] Ошибка вычисления диффа расписания: ${e.message}`);
    }

    await VersionModel.create({
        [keyField]: key,
        hash: beforeHash,
        data: before.data,
        validFrom: before.changedAt || before.createdAt || before.updatedAt || now,
        validTo: now,
        diffSummary: diffResult?.summary || "",
        diffDetails: diffResult?.changes || [],
        archivedAt: now,
    });

    if (typeof afterArchive === "function") {
        await afterArchive();
    }

    return { status: "changed", hash };
}
