"""FastAPI application for Kichobot.

Unified HTTP server providing:
- Lifespan management (Beanie DB initialization, bot commands, webhook, APScheduler).
- Root healthcheck (GET /) for Render.
- Health monitor (GET /health, GET /bot/health).
- Telegram Webhook receiver (POST /bot/webhook) with immediate 200 OK and secret token validation.
- Client log endpoint (POST /bot/log) with 10 req/min rate limit and 2KB payload guard.
- User schedule endpoint (GET /bot/get_user_schedule) with 45 req/min rate limit.
- External cron trigger (POST /api/cron/sync) for wake-up pingers.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time
from typing import Any

from aiogram.types import BotCommand, Update
import orjson
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse


class ORJSONResponse(Response):
    """High-performance JSON response using orjson serialization."""

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        if content is None:
            return b""
        if isinstance(content, bytes):
            return content
        return orjson.dumps(content)


from src.api.health import health_monitor
from src.api.rate_limiter import log_rate_limiter, schedule_rate_limiter
from src.bot.bot_instance import get_bot, get_dispatcher
from src.core.config import settings
from src.core.logging import get_logger
from src.cron import (
    get_scheduler,
    run_daily_data_update,
    setup_all_crons,
    smart_warmup_engine,
    start_scheduler,
    stop_scheduler,
    warmup_top_schedules,
)
from src.database.connection import close_db, init_db
from src.services.buketov_api import buketov_api_service
from src.services.schedule_adapter import schedule_api_adapter

logger = get_logger("api_app")

# Retain strong references to background tasks to prevent Python GC collection mid-flight
_background_tasks: set[asyncio.Task[Any]] = set()


def _create_background_task(coro: Any) -> asyncio.Task[Any]:
    """Schedules a coroutine as a background task retaining a strong reference."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def get_client_ip(request: Request) -> str:
    """Extracts client IP address respecting reverse proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages application startup and graceful shutdown."""
    # === STARTUP ===
    logger.info("Initializing Kichobot services in lifespan manager...")
    try:
        await init_db()
        from src.services.black_list_service import black_list_service
        from src.services.user_toll_service import user_toll_service

        await black_list_service.ensure_loaded()
        await user_toll_service.ensure_loaded()
    except Exception as e:
        logger.error(f"Database initialization error during startup: {e}")

    bot = get_bot()
    dp = get_dispatcher()

    # Register Telegram bot commands and descriptions
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Меню / Мәзір")
        ])
        await bot.set_my_description(description="Kicho — КарУ (Букетов) кестесі 📚")
        await bot.set_my_short_description(short_description="Kicho — КарУ (Букетов) кестесі 📚")
        logger.info("Bot commands and descriptions registered.")
    except Exception as e:
        logger.warning(f"Failed to set bot commands/description: {e}")

    # Register webhook if configured for webhook mode
    if settings.BOT_MODE == "webhook":
        webhook_url = settings.webhook_url
        if webhook_url:
            secret = settings.resolved_webhook_secret
            logger.info(f"Configuring Telegram webhook on URL: {webhook_url}")
            try:
                await bot.set_webhook(
                    url=webhook_url,
                    secret_token=secret,
                    drop_pending_updates=False,
                    allowed_updates=dp.resolve_used_update_types(),
                )
                logger.info("Telegram webhook configured successfully with drop_pending_updates=False.")
            except Exception as e:
                logger.warning(f"Could not register Telegram webhook: {e}")
        else:
            logger.warning("BOT_MODE is 'webhook' but WEBHOOK_DOMAIN is not specified.")

    # Start APScheduler
    try:
        scheduler = get_scheduler()
        setup_all_crons(scheduler)
        start_scheduler()
    except Exception as e:
        logger.error(f"Error starting APScheduler: {e}")

    # Start SmartWarmupEngine (paced background warmup: TOP-150 groups, TOP-30 teachers)
    try:
        smart_warmup_engine.start()
    except Exception as e:
        logger.error(f"Error starting SmartWarmupEngine: {e}")

    yield

    # === SHUTDOWN ===
    logger.info("Shutting down Kichobot services...")
    try:
        await smart_warmup_engine.stop_async()
    except Exception as e:
        logger.warning(f"Error stopping SmartWarmupEngine: {e}")

    try:
        stop_scheduler()
    except Exception as e:
        logger.warning(f"Error stopping scheduler: {e}")

    try:
        from src.bot.middlewares.user_action_logger import user_action_logging_middleware
        await user_action_logging_middleware.flush()
    except Exception as e:
        logger.warning(f"Error flushing user actions during shutdown: {e}")

    if _background_tasks:
        try:
            await asyncio.gather(*_background_tasks, return_exceptions=True)
        except Exception as e:
            logger.warning(f"Error draining background tasks: {e}")

    try:
        await bot.session.close()
    except Exception as e:
        logger.warning(f"Error closing bot session: {e}")

    try:
        await close_db()
    except Exception as e:
        logger.warning(f"Error closing database connection: {e}")

    logger.info("Shutdown completed cleanly.")


def create_app() -> FastAPI:
    """Creates and configures the FastAPI application."""
    app = FastAPI(
        title="Kichobot API",
        version="2.0.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 1. Root healthcheck for Render
    @app.get("/", response_class=PlainTextResponse)
    async def root_health() -> PlainTextResponse:
        return PlainTextResponse("Kichobot is alive! 🤖")

    # 2. Detailed health endpoints (/health and /bot/health)
    @app.get("/health")
    @app.get("/bot/health")
    async def get_health_status() -> ORJSONResponse:
        try:
            status_data = await health_monitor.is_healthy()
            status_code = 200 if status_data.get("healthy") else 503
            return ORJSONResponse(content=status_data, status_code=status_code)
        except Exception as e:
            logger.error(f"Health check endpoint failure: {e}")
            return ORJSONResponse(
                content={"status": "error", "error": str(e)},
                status_code=500,
            )

    # 3. Telegram Webhook endpoint
    @app.post("/bot/webhook")
    async def process_telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> ORJSONResponse:
        # Secret Token verification when in webhook mode
        token = x_telegram_bot_api_secret_token or request.headers.get("x-telegram-bot-api-secret-token")
        if settings.BOT_MODE == "webhook":
            expected_token = settings.resolved_webhook_secret
            if expected_token and token != expected_token:
                logger.warning(f"[Webhook Security] Unauthorized webhook update attempt from {get_client_ip(request)}")
                return ORJSONResponse(content={"error": "Forbidden"}, status_code=403)

        # Update bot activity timestamp for health monitoring
        health_monitor.update_activity()

        try:
            payload = await request.json()
        except Exception:
            return ORJSONResponse(content={"ok": True}, status_code=200)

        if not payload or not isinstance(payload, dict):
            return ORJSONResponse(content={"ok": True}, status_code=200)

        # Dispatch update to aiogram asynchronously (zero downtime, immediate 200 OK)
        bot = get_bot()
        dp = get_dispatcher()

        async def _feed() -> None:
            try:
                update_obj = Update.model_validate(payload, context={"bot": bot})
                await dp.feed_update(bot=bot, update=update_obj)
            except Exception as e:
                logger.error(f"Error processing webhook update in background: {e}")

        _create_background_task(_feed())
        return ORJSONResponse(content={"ok": True}, status_code=200)

    # 4. Client log endpoint
    @app.post("/bot/log")
    async def post_client_log(request: Request) -> ORJSONResponse:
        client_ip = get_client_ip(request)
        if not log_rate_limiter.is_allowed(client_ip):
            logger.warning(f"[Rate Limit] Exceeded log rate limit for IP: {client_ip}")
            return ORJSONResponse(
                content={"error": "Слишком много запросов. Пожалуйста, подождите минуту."},
                status_code=429,
            )

        body_bytes = await request.body()
        if not body_bytes or not body_bytes.strip():
            return ORJSONResponse(content={"error": "Empty body"}, status_code=400)

        if len(body_bytes) > 2048:
            return ORJSONResponse(
                content={"error": "Payload too large (max 2KB)"},
                status_code=413,
            )

        body_text = body_bytes.decode("utf-8", errors="replace")
        logger.info(f"[Client Log] {body_text}")
        return ORJSONResponse(content="logged", status_code=200)

    # 5. User schedule endpoint
    @app.get("/bot/get_user_schedule")
    async def get_user_schedule(
        request: Request,
        user_id: str | None = Query(default=None, alias="userId"),
    ) -> ORJSONResponse:
        client_ip = get_client_ip(request)
        if not schedule_rate_limiter.is_allowed(client_ip):
            logger.warning(f"[Rate Limit] Exceeded schedule rate limit for IP: {client_ip}")
            return ORJSONResponse(
                content={"error": "Слишком много запросов. Пожалуйста, подождите минуту."},
                status_code=429,
            )

        if not user_id:
            return ORJSONResponse(
                content="Не указан или некорректен ID пользователя",
                status_code=400,
            )

        try:
            numeric_user_id = int(user_id)
            if numeric_user_id <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return ORJSONResponse(
                content="Некорректный userId",
                status_code=400,
            )

        from src.database.models.group import Group
        from src.database.models.schedule import Schedule
        from src.database.models.teacher import Teacher
        from src.database.models.teacher_schedule import TeacherSchedule
        from src.database.models.user import User

        user = await User.find_one({"userId": numeric_user_id})
        if not user:
            return ORJSONResponse(
                content={"studentSchedule": None, "teacherSchedule": None},
                status_code=200,
            )

        data: dict[str, Any] = {
            "scheduleType": user.schedule_type or "student",
            "studentSchedule": None,
            "teacherSchedule": None,
        }

        # Student schedule lookup
        if user.group:
            group = await Group.find_one({"id": user.group})
            if group:
                try:
                    sched = await buketov_api_service.fetch_schedule(
                        {"groupId": group.group_id, "group": group.name},
                        cache_type="student",
                    )
                    records = sched.get("records") if isinstance(sched, dict) else sched
                    adapted = schedule_api_adapter.adapt_group_schedule(records, user.language or "рус")
                    data["studentSchedule"] = {
                        "schedule": adapted,
                        "updatedAt": int(time.time() * 1000),
                        "group": group.model_dump(by_alias=True, mode="json"),
                        "isNew": True,
                    }
                except Exception:
                    db_sched = await Schedule.find_one({"groupId": group.group_id})
                    if db_sched:
                        raw_records = getattr(db_sched, "data", None) or getattr(db_sched, "schedule", None) or []
                        serialized = [
                            d.model_dump(by_alias=True, mode="json") if hasattr(d, "model_dump") else d
                            for d in raw_records
                        ]
                        data["studentSchedule"] = {
                            "schedule": serialized,
                            "updatedAt": int(db_sched.updated_at.timestamp() * 1000) if db_sched.updated_at else int(time.time() * 1000),
                            "group": group.model_dump(by_alias=True, mode="json"),
                            "isNew": False,
                        }

        # Teacher schedule lookup
        if user.teacher:
            teacher = await Teacher.find_one({"id": user.teacher})
            if teacher:
                try:
                    sched = await buketov_api_service.fetch_teacher_schedule(teacher.name)
                    records = sched.get("records") if isinstance(sched, dict) else sched
                    adapted = schedule_api_adapter.adapt_teacher_schedule(records, user.language or "рус")
                    data["teacherSchedule"] = {
                        "schedule": adapted,
                        "updatedAt": int(time.time() * 1000),
                        "teacher": teacher.model_dump(by_alias=True, mode="json"),
                        "isNew": True,
                    }
                except Exception:
                    db_sched = await TeacherSchedule.find_one({"teacherId": teacher.teacher_id})
                    if db_sched:
                        raw_records = getattr(db_sched, "data", None) or getattr(db_sched, "schedule", None) or []
                        serialized = [
                            d.model_dump(by_alias=True, mode="json") if hasattr(d, "model_dump") else d
                            for d in raw_records
                        ]
                        data["teacherSchedule"] = {
                            "schedule": serialized,
                            "updatedAt": int(db_sched.updated_at.timestamp() * 1000) if db_sched.updated_at else int(time.time() * 1000),
                            "teacher": teacher.model_dump(by_alias=True, mode="json"),
                            "isNew": False,
                        }

        return ORJSONResponse(content=data, status_code=200)

    # 6. Protected external pinger cron trigger endpoint
    @app.post("/api/cron/sync")
    async def trigger_cron_sync(
        request: Request,
        key: str | None = Query(default=None),
        x_cron_key: str | None = Header(default=None),
    ) -> ORJSONResponse:
        provided_key = key or x_cron_key or request.headers.get("x-cron-key")
        expected_key = settings.resolved_webhook_secret or settings.WEBHOOK_SECRET_TOKEN

        if not expected_key or provided_key != expected_key:
            logger.warning("[Cron Sync Security] Unauthorized attempt to trigger cron sync")
            return ORJSONResponse(content={"error": "Unauthorized"}, status_code=403)

        logger.info("[Cron Sync] Triggering background daily sync and warmup...")
        _create_background_task(run_daily_data_update())
        _create_background_task(warmup_top_schedules())

        return ORJSONResponse(
            content={"ok": True, "message": "Daily sync triggered"},
            status_code=200,
        )

    return app


# Singleton application instance
app = create_app()
