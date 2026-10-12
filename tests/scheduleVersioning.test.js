import test from 'node:test';
import assert from 'node:assert/strict';
import {
    computeScheduleHash,
    projectGroupSchedule,
    projectTeacherSchedule,
    saveVersioned
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
    const createdAt = new Date('2026-01-01');
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

test('saveVersioned: history is trimmed to maxVersions, newest kept', async () => {
    const f = makeFakes('groupId');
    const args = { Model: f.Model, VersionModel: f.VersionModel, keyField: 'groupId', key: 1, project: projectGroupSchedule, maxVersions: 3 };
    for (let i = 0; i < 7; i++) {
        await saveVersioned({ ...args, data: day('v' + i) });
        await new Promise(r => setTimeout(r, 2));
    }
    assert.equal(f.store.versions.length, 3);
    const subjects = f.store.versions.map(v => v.data[0].subjects[0].subject).sort();
    assert.deepEqual(subjects, ['v3', 'v4', 'v5']);
});

test('schedule history models have NO TTL index and carry hash fields', () => {
    for (const M of [Schedule, ScheduleVersion, TeacherSchedule, TeacherScheduleVersion]) {
        const ttl = M.schema.indexes().filter(([, opts]) => opts && opts.expireAfterSeconds !== undefined);
        assert.equal(ttl.length, 0, `${M.modelName} must not expire`);
    }
    assert.ok(Schedule.schema.path('hash'));
    assert.ok(TeacherSchedule.schema.path('hash'));
    assert.ok(ScheduleVersion.schema.path('validTo'));
    assert.ok(TeacherScheduleVersion.schema.path('validTo'));
});
