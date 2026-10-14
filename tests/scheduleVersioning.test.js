import test from 'node:test';
import assert from 'node:assert/strict';
import {
    computeScheduleHash,
    projectGroupSchedule,
    projectTeacherSchedule,
    saveVersioned,
    enforceHistorySizeLimit,
    HISTORY_MAX_BYTES,
    HISTORY_TTL_SECONDS
} from '../bot/services/scheduleVersioning.js';
import { Schedule, ScheduleVersion } from '../bot/models/schedule.js';
import { TeacherSchedule, TeacherScheduleVersion } from '../bot/models/teacherSchedule.js';

// Минимальные in-memory «модели», повторяющие используемые saveVersioned методы Mongoose
function makeFakes(keyField) {
    const store = { doc: null, versions: [] };
    let idc = 1;
    const Model = {
        async updateOne(filter, upd) {
            const d = store.doc;
            if (!d) return { matchedCount: 0 };
            for (const [k, v] of Object.entries(filter)) if (d[k] !== v) return { matchedCount: 0 };
            Object.assign(d, upd.$set, { updatedAt: new Date() });
            return { matchedCount: 1 };
        },
        async findOneAndUpdate(filter, upd) {
            const before = store.doc ? JSON.parse(JSON.stringify(store.doc)) : null;
            store.doc = { ...(store.doc || { _id: idc++, createdAt: new Date() }), ...upd.$set, updatedAt: new Date() };
            return before;
        }
    };
    const VersionModel = {
        async create(v) { store.versions.push({ _id: idc++, ...v }); },
        find() {
            let arr = [...store.versions];
            const chain = {
                sort() { arr.sort((a, b) => b.validTo - a.validTo); return chain; },
                skip(n) { arr = arr.slice(n); return chain; },
                select() { return chain; },
                async lean() { return arr; }
            };
            return chain;
        },
        async deleteMany({ _id }) { store.versions = store.versions.filter(v => !_id.$in.includes(v._id)); }
    };
    return { store, Model, VersionModel, keyField };
}

const day = (subject) => [{ day: 'Понедельник', subjects: [{ time: '8.30-9.20', subject }] }];

test('computeScheduleHash: identical content -> same hash, one changed letter -> different hash', () => {
    const a = computeScheduleHash(projectGroupSchedule(day('Математика, ауд. 101')));
    const b = computeScheduleHash(projectGroupSchedule(day('Математика, ауд. 101')));
    const c = computeScheduleHash(projectGroupSchedule(day('Математика, ауд. 102')));
    assert.equal(a, b);
    assert.notEqual(a, c);
});

test('projectGroupSchedule / projectTeacherSchedule ignore fields that are not stored in the schema', () => {
    const g1 = projectGroupSchedule([{ day: 'Пн', extra: 1, subjects: [{ time: 't', subject: 's', foo: 'x' }] }]);
    const g2 = projectGroupSchedule([{ day: 'Пн', subjects: [{ time: 't', subject: 's' }] }]);
    assert.deepEqual(g1, g2);

    const t1 = projectTeacherSchedule([{ day: 'Пн', groups: [{ time: 't', group: 'g', subject: 's', lessonType: 'л', groupsList: ['g'] }] }]);
    const t2 = projectTeacherSchedule([{ day: 'Пн', groups: [{ time: 't', group: 'g', subject: 's', lessonType: 'л' }] }]);
    assert.deepEqual(t1, t2);
});

test('saveVersioned: first save creates, identical re-save does NOT duplicate or archive', async () => {
    const f = makeFakes('groupId');
    const args = { Model: f.Model, VersionModel: f.VersionModel, keyField: 'groupId', key: 5, project: projectGroupSchedule };

    const r1 = await saveVersioned({ ...args, data: day('Физика') });
    assert.equal(r1.status, 'created');
    const changedAt = f.store.doc.changedAt;

    const r2 = await saveVersioned({ ...args, data: day('Физика') });
    assert.equal(r2.status, 'unchanged');
    assert.equal(f.store.versions.length, 0, 'nothing archived');
    assert.equal(f.store.doc.changedAt, changedAt, 'content change date untouched');
    assert.ok(f.store.doc.checkedAt, 'check time updated');
});

test('saveVersioned: one changed letter -> new original becomes active, old one is archived (not deleted)', async () => {
    const f = makeFakes('groupId');
    const args = { Model: f.Model, VersionModel: f.VersionModel, keyField: 'groupId', key: 5, project: projectGroupSchedule };

    await saveVersioned({ ...args, data: day('Физика') });
    const r = await saveVersioned({ ...args, data: day('Физики') });

    assert.equal(r.status, 'changed');
    assert.equal(f.store.versions.length, 1);
    assert.equal(f.store.versions[0].data[0].subjects[0].subject, 'Физика', 'old original preserved');
    assert.equal(f.store.doc.data[0].subjects[0].subject, 'Физики', 'new original active');
    assert.equal(f.store.doc.hash, computeScheduleHash(projectGroupSchedule(day('Физики'))));

    // повторное сохранение того же нового содержимого — без новых версий
    await saveVersioned({ ...args, data: day('Физики') });
    assert.equal(f.store.versions.length, 1);
});

test('saveVersioned: legacy doc without hash and same content is not archived; changed content is', async () => {
    const f = makeFakes('teacherId');
    const args = { Model: f.Model, VersionModel: f.VersionModel, keyField: 'teacherId', key: 9, project: projectTeacherSchedule };
    const legacyData = [{ day: 'Пн', groups: [{ time: 't', group: 'g', subject: 's', lessonType: 'л' }] }];
    const createdAt = new Date('2026-09-10T10:00:00.000Z');
    f.store.doc = { _id: 1, teacherId: 9, data: legacyData, createdAt, updatedAt: createdAt };

    // то же содержимое, но с лишним (не хранимым) полем groupsList
    const same = [{ day: 'Пн', groups: [{ time: 't', group: 'g', subject: 's', lessonType: 'л', groupsList: ['g'] }] }];
    const r1 = await saveVersioned({ ...args, data: same });
    assert.equal(r1.status, 'unchanged');
    assert.equal(f.store.versions.length, 0);
    assert.equal(new Date(f.store.doc.changedAt).getTime(), createdAt.getTime());

    const changed = [{ day: 'Пн', groups: [{ time: 't', group: 'g', subject: 's2', lessonType: 'л' }] }];
    const r2 = await saveVersioned({ ...args, data: changed });
    assert.equal(r2.status, 'changed');
    assert.equal(f.store.versions.length, 1);
    assert.equal(f.store.versions[0].data[0].groups[0].subject, 's');
});

test('resolveValidFrom and normalizeDayCanonical work correctly', async () => {
    const { resolveValidFrom, normalizeDayCanonical, SEMESTER_START_DATE } = await import('../bot/services/scheduleVersioning.js');
    // Даты из лета привязываются к началу семестра (01.09.2026)
    assert.equal(resolveValidFrom(new Date('2026-06-04T15:30:00Z')).getTime(), SEMESTER_START_DATE.getTime());
    assert.equal(resolveValidFrom(new Date('2026-08-25T12:00:00Z')).getTime(), SEMESTER_START_DATE.getTime());
    // Даты после начала семестра сохраняются как есть
    const septDate = new Date('2026-09-15T12:00:00Z');
    assert.equal(resolveValidFrom(septDate).getTime(), septDate.getTime());

    // Дни недели приводятся к каноническому номеру 1..6 независимо от языка (RU/KZ)
    assert.equal(normalizeDayCanonical('Понедельник'), '1');
    assert.equal(normalizeDayCanonical('Дүйсенбі'), '1');
    assert.equal(normalizeDayCanonical('Вторник'), '2');
    assert.equal(normalizeDayCanonical('Сейсенбі'), '2');
    assert.equal(normalizeDayCanonical('Суббота'), '6');
    assert.equal(normalizeDayCanonical('Сенбі'), '6');
});

test('saveVersioned: archived version gets archivedAt and afterArchive hook is called only on real change', async () => {
    const f = makeFakes('groupId');
    let hookCalls = 0;
    const args = {
        Model: f.Model, VersionModel: f.VersionModel, keyField: 'groupId', key: 1,
        project: projectGroupSchedule, afterArchive: async () => { hookCalls++; }
    };
    await saveVersioned({ ...args, data: day('a') });   // created
    await saveVersioned({ ...args, data: day('a') });   // unchanged
    assert.equal(hookCalls, 0);
    await saveVersioned({ ...args, data: day('b') });   // changed
    assert.equal(hookCalls, 1);
    assert.ok(f.store.versions[0].archivedAt instanceof Date, 'archivedAt is set (drives the 2-month TTL)');
});

// Фейковая коллекция истории с заданным размером для проверки лимита
function makeHistoryColl(name, docs, bytesPerDoc) {
    const state = { docs: docs.map((archivedAt, i) => ({ _id: `${name}${i}`, archivedAt })) };
    return {
        state,
        collection: { collectionName: name },
        db: { db: { command: async ({ collStats }) => {
            if (collStats !== name) throw new Error('unexpected collection');
            return { size: state.docs.length * bytesPerDoc, count: state.docs.length };
        } } },
        find() {
            let arr = [...state.docs];
            const chain = {
                sort() { arr.sort((a, b) => a.archivedAt - b.archivedAt); return chain; },
                limit(n) { arr = arr.slice(0, n); return chain; },
                select() { return chain; },
                async lean() { return arr; }
            };
            return chain;
        },
        async deleteMany({ _id }) { state.docs = state.docs.filter(d => !_id.$in.includes(d._id)); }
    };
}

test('enforceHistorySizeLimit: under the limit nothing is deleted', async () => {
    const A = makeHistoryColl('a', [1, 2, 3], 1000);
    const B = makeHistoryColl('b', [1, 2], 1000);
    const r = await enforceHistorySizeLimit([A, B], 10_000, { force: true });
    assert.equal(r.deleted, 0);
    assert.equal(A.state.docs.length, 3);
    assert.equal(B.state.docs.length, 2);
});

test('enforceHistorySizeLimit: over the limit the OLDEST versions are deleted until it fits', async () => {
    // 100 docs x 1000 B = 100 000 B в одной коллекции, лимит 50 000 B -> должно остаться ~50 самых новых
    const A = makeHistoryColl('a', Array.from({ length: 100 }, (_, i) => i), 1000);
    const B = makeHistoryColl('b', [], 1000);
    const r = await enforceHistorySizeLimit([A, B], 50_000, { force: true });
    assert.equal(r.deleted, 50);
    assert.equal(A.state.docs.length, 50);
    const remaining = A.state.docs.map(d => d.archivedAt);
    assert.equal(Math.min(...remaining), 50, 'oldest 50 removed, newest 50 kept');
});

test('enforceHistorySizeLimit: limit is shared across both history collections', async () => {
    const A = makeHistoryColl('a', Array.from({ length: 60 }, (_, i) => i), 1000);
    const B = makeHistoryColl('b', Array.from({ length: 60 }, (_, i) => i), 1000);
    // всего 120 000 B, лимит 60 000 B -> суммарно должно остаться около 60 документов
    await enforceHistorySizeLimit([A, B], 60_000, { force: true });
    const total = (A.state.docs.length + B.state.docs.length) * 1000;
    assert.ok(total <= 60_000, `total ${total} must fit in 60000`);
    assert.ok(total >= 58_000, `should not delete far more than needed (${total})`);
});

test('history limits: 50 MB total and 2-month TTL on history; main schedule collections never expire', () => {
    assert.equal(HISTORY_MAX_BYTES, 50 * 1024 * 1024);
    assert.equal(HISTORY_TTL_SECONDS, 60 * 24 * 60 * 60);

    for (const M of [Schedule, TeacherSchedule]) {
        const ttl = M.schema.indexes().filter(([, opts]) => opts && opts.expireAfterSeconds !== undefined);
        assert.equal(ttl.length, 0, `${M.modelName} (актуальный оригинал) must not expire`);
    }
    for (const M of [ScheduleVersion, TeacherScheduleVersion]) {
        const ttl = M.schema.indexes().filter(([, opts]) => opts && opts.expireAfterSeconds !== undefined);
        assert.equal(ttl.length, 1, `${M.modelName} must have exactly one TTL index`);
        assert.deepEqual(Object.keys(ttl[0][0]), ['archivedAt']);
        assert.equal(ttl[0][1].expireAfterSeconds, HISTORY_TTL_SECONDS, '2 months');
    }
    assert.ok(Schedule.schema.path('hash'));
    assert.ok(TeacherSchedule.schema.path('hash'));
    assert.ok(ScheduleVersion.schema.path('validTo'));
    assert.ok(TeacherScheduleVersion.schema.path('validTo'));
});

