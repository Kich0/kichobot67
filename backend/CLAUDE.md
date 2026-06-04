# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Node.js Express API service that scrapes and provides schedule data from a KSU (Kazakh university) website using Puppeteer. The service authenticates with the university portal, extracts schedule information for faculties/programs/groups, and exposes it via REST endpoints.

## Core Architecture

### Browser Management & Web Scraping
- **BrowserController** (`controllers/BrowserController.js`) - Singleton that manages a persistent Puppeteer browser instance
  - Handles authentication to `schedule.buketov.edu.kz`
  - Manages browser lifecycle (launch, restart, connection checks)
  - Supports proxy configuration and headless/debug modes
  - Auto-authenticates on startup if `AUTO_KSU_AUTH=true`
  - **Memory Protection**: Middleware `allChecksCall` monitors open pages and triggers cleanup/restart at thresholds (10/20 pages)
  - **Recovery Lock**: `isRecovering` flag prevents concurrent recovery attempts when KSU is unresponsive
  - **Launch Lock**: `isLaunching` flag prevents concurrent browser launches - second request waits for first to finish
  - **Request Blocking**: Blocks incoming requests during browser launch (`isLaunching`), authentication (`isAuthing`), and recovery (`isRecovering`) to prevent memory overload

- **ScheduleService** (`services/ScheduleService.js`) - Core scraping logic
  - `get_faculty_list()` - Authenticates and extracts faculties
  - `get_program_list_by_facultyId()` - Scrapes programs for a faculty
  - `get_group_list_by_programId()` - Scrapes groups for a program
  - `get_schedule_by_groupId()` - Scrapes schedule table, converts to JSON
  - Implements retry logic and error recovery (browser restart on 403/malformed data)
  - Takes screenshots of errors to `logs/error_*.png`

### Authentication & Authorization
- JWT-based auth with access/refresh tokens stored in cookies
- **authMiddleware** (`middlewares/authMiddleware.js`) - Validates JWT from cookies
- **roleMiddleware** (`middlewares/roleMiddleware.js`) - Role-based access control
- Users have roles (Admin, etc.) defined in MongoDB

### Data Models (Mongoose)
- **User** - username, password (bcrypt), roles
- **Token** - refresh tokens
- **Group** - schedule group data cached from KSU
- **Log** - application logs (also stored via Winston)
- **Role** - user roles

### Logging
- Winston logger (`logging/logging.js`) with multiple transports:
  - Console (all levels)
  - `logs.log` (all levels)
  - `error_logs.log` (errors only)
  - Daily rotating files in `logs/` directory
  - Custom Telegram transport for warnings/errors (when not in DEBUG mode)

### Scheduled Tasks (Cron)
- `cron/ksuReAuth.js` - Re-authenticates to KSU every hour (`0 * * * *`)
- `cron/loggingPathUpdate.js` - Rotates log files daily (`0 0 * * *`)

## Development Commands

```bash
# Development (with nodemon auto-reload)
npm run dev

# Production
npm start

# PM2 deployment (uses ecosystem.config.cjs)
pm2 start ecosystem.config.cjs
```

## Key Configuration (.env)

Required environment variables (see `config.js`):
- `PORT` - API server port
- `DB_URI` - MongoDB connection string
- `JWT_ACCESS_SECRET`, `JWT_REFRESH_SECRET` - JWT secrets
- `KSU_DOMAIN`, `KSU_LOGIN`, `KSU_PASSWORD` - University portal credentials
- `DEBUG` - Set to "true" for visible browser and enhanced logging
- `START_BROWSER` - Set to "true" to launch browser on startup
- `AUTO_KSU_AUTH` - Set to "true" to auto-authenticate on startup
- `USE_PROXY` - Set to "true" to enable proxy
- `HTTP_PROXY`, `PROXY_LOGIN`, `PROXY_PASSWORD` - Proxy settings
- `TG_TOKEN`, `LOG_CHANEL_ID`, `LOGGER_TG_TOKEN` - Telegram logging

## API Structure

Base path: `/express/api`

Main routers:
- `/auth` - User registration/login/logout/refresh
- `/schedule` - Schedule scraping endpoints (faculty/program/group/schedule)
- `/teacher`, `/teacherSchedule` - Teacher-related endpoints
- `/groups` - Group management
- `/users` - User management (admin)
- `/logs` - Log access
- `/browser` - Browser control (restart, screenshots)
- `/gpt` - GPT assistant integration
- `/converter` - Conversion utilities

All schedule routes require browser to be ready via `BrowserController.allChecksCall` middleware.

## Important Implementation Details

### Browser Stability & Memory Management
- If KSU returns 403 or malformed data, the service restarts the browser and retries
- Browser runs in headless mode in production (`executablePath: '/usr/bin/google-chrome-stable'`)
- In DEBUG mode, browser runs with visible GUI for debugging
- **Memory leak prevention**: All `page.newPage()` calls MUST be wrapped in try/finally with `page.close()` in finally block
- **Recovery mechanism**:
  - `authIfNot()` uses `isRecovering` lock to prevent concurrent recovery attempts
  - Only ONE recovery attempt runs at a time, others are rejected immediately
  - Auto-timeout after 60 seconds if recovery hangs
  - Always runs re-auth even on errors to maximize recovery chances
- **Request throttling**: Incoming requests are blocked during auth/recovery to prevent memory exhaustion

### Schedule Parsing
- Schedule tables are scraped as HTML, converted to JSON via `HtmlService.htmlTableToJson()`
- Supports both Russian and Kazakh languages
- Trims empty subjects and handles `<br>` tags in subject names
- Detects malformed schedules (e.g., subjects with only `\n`) and triggers browser restart

### Error Handling
- Custom `ApiError` exceptions (`exceptions/apiError.js`)
- Global error middleware (`middlewares/errorMiddleware.js`)
- Screenshots saved to `logs/` on scraping errors
- All errors logged to Winston (console + files + optional Telegram)

## Module System

Uses ES modules (`"type": "module"` in package.json) - all imports must use `.js` extensions.
