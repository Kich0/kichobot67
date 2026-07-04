import 'dotenv/config';
import mongoose from 'mongoose';
import BrowserController from './controllers/BrowserController.js';
import ScheduleService from './services/ScheduleService.js';

async function run() {
    console.log('Connecting to KICHOBOT DB...');
    await mongoose.connect(process.env.DB_URI);
    console.log('Connected to DB!');

    await BrowserController.launchBrowser();
    await BrowserController.authIfNot();
    const browser = BrowserController.browser;
    
    let faculties = [];
    let authObj = null;
    try {
        authObj = await ScheduleService.get_faculty_list(browser);
        faculties = authObj.faculties_data;
        console.log(`Found ${faculties.length} faculties!`);
    } catch(e) {
        console.log('Auth error:', e.message);
        process.exit(1);
    }

    const facultiesCol = mongoose.connection.collection('faculties');
    await facultiesCol.deleteMany({});
    if (faculties.length > 0) {
        await facultiesCol.insertMany(faculties);
    }

    let allPrograms = [];
    for (const faculty of faculties) {
        try {
            console.log(`Fetching programs for faculty: ${faculty.name}`);
            const programs = await ScheduleService.get_program_list_by_facultyId(browser, faculties, faculty.id);
            for (const p of programs) {
                allPrograms.push({
                    name: p.name,
                    id: p.id,
                    href: p.href,
                    faculty: faculty.id,
                    facultyName: faculty.name
                });
            }
        } catch(e) {
            console.error(`Error for faculty ${faculty.id}: ${e.message}`);
        }
        await new Promise(r => setTimeout(r, 100));
    }

    const programsCol = mongoose.connection.collection('programs');
    await programsCol.deleteMany({});
    if (allPrograms.length > 0) {
        await programsCol.insertMany(allPrograms);
    }
    console.log(`Found ${allPrograms.length} programs in DB.`);

    let allGroups = [];
    for (const program of allPrograms) {
        try {
            console.log(`Fetching groups for program: ${program.name}`);
            const groups = await ScheduleService.get_group_list_by_programId(browser, program.id);
            if (groups && groups.length > 0) {
                const mappedGroups = groups.map(g => ({
                    name: g.name,
                    id: g.id,
                    program: program.id,
                    language: g.language,
                    age: g.age,
                    href: g.href,
                    studentCount: g.studentCount,
                    programName: program.name
                }));
                allGroups.push(...mappedGroups);
            }
        } catch (e) {
            console.error(`Error for program ${program.id}: ${e.message}`);
        }
        await new Promise(r => setTimeout(r, 100));
    }

    console.log(`Found ${allGroups.length} total groups. Inserting...`);
    const groupsCol = mongoose.connection.collection('groups');
    await groupsCol.deleteMany({});
    if (allGroups.length > 0) {
        await groupsCol.insertMany(allGroups);
    }
    console.log('Inserted groups to KICHOBOT database!');

    console.log('SYNC EVERYTHING DONE!');
    process.exit(0);
}
run();
