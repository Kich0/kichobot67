import './loadEnv.js';
import {updateFacultiesCommandController} from "./bot/controllers/commands/adminCommands/updateFaculties.js";
import {updateProgramsCommandController} from "./bot/controllers/commands/adminCommands/updatePrograms.js";
import {updateGroupsCommandController} from "./bot/controllers/commands/adminCommands/updateGroups.js";
import {updateProfilesCommandController} from "./bot/controllers/commands/adminCommands/updateProfiles.js";
import {updateDepartmentsCommandController} from "./bot/controllers/commands/adminCommands/updateDepartments.js";
import {updateTeachersCommandController} from "./bot/controllers/commands/adminCommands/updateTeachers.js";
import {updateSchedulesCommandController} from "./bot/controllers/commands/adminCommands/updateSchedules.js";
import db from "./bot/db/connection.js";
import config from "./bot/config.js";

async function runUpdate() {
    try {
        console.log("Connecting to DB...");
        if (!config.DB_URI) {
            throw new Error("config.DB_URI is undefined. Check your .env file and paths.");
        }
        await db.connect(config.DB_URI);
        console.log("Starting full database update from KSU...");
        
        console.log("Updating Faculties...");
        await updateFacultiesCommandController(true);
        
        console.log("Updating Programs...");
        await updateProgramsCommandController(true);
        
        console.log("Updating Groups...");
        await updateGroupsCommandController(true);
        
        console.log("Updating Departments...");
        await updateDepartmentsCommandController(true);
        
        console.log("Updating Teachers...");
        await updateTeachersCommandController(true);

        console.log("Updating Schedules...");
        await updateSchedulesCommandController(true);
        
        console.log("Update finished successfully!");
        process.exit(0);
    } catch (e) {
        console.error("Update failed:", e);
        process.exit(1);
    }
}

runUpdate();
