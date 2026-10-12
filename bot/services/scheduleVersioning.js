import crypto from "crypto";

/**
 * Версионирование расписаний.
 *
 * Основная коллекция (Schedule / TeacherSchedule) хранит ОДИН актуальный «оригинал» на группу/преподавателя
 * (его бот показывает при падении API). Если содержимое расписания изменилось хоть на одну букву —
 * предыдущий оригинал уходит в коллекцию истории (ScheduleVersion / TeacherScheduleVersion), а новый
 * становится актуальным. Если ничего не изменилось — запись только «трогается» (обновляется время проверки).
 */

// Страховочный лимит истории на одну группу/преподавателя (самые старые версии сверх лимита удаляются,
// чтобы бесплатная Atlas-база 512 МБ не переполнилась). Реально расписание меняется единицы раз за семестр.
export const MAX_VERSIONS_PER_ENTITY = 100;

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
    maxVersions = MAX_VERSIONS_PER_ENTITY,
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

    // 3. Содержимое изменилось — старый оригинал не удаляем, а сохраняем в историю.
    await VersionModel.create({
        [keyField]: key,
        hash: beforeHash,
        data: before.data,
        validFrom: before.changedAt || before.createdAt || before.updatedAt || now,
        validTo: now,
    });

    // 4. Страховочная обрезка самых старых версий сверх лимита.
    const extra = await VersionModel.find({ [keyField]: key })
        .sort({ validTo: -1 })
        .skip(maxVersions)
        .select("_id")
        .lean();
    if (extra.length > 0) {
        await VersionModel.deleteMany({ _id: { $in: extra.map((e) => e._id) } });
    }

    return { status: "changed", hash };
}
