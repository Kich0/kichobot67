"""Unit and integration tests for Phase 6 FastAPI application and endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from httpx import ASGITransport
import pytest

from src.api.app import app, create_app, lifespan
from src.api.health import health_monitor
from src.api.rate_limiter import log_rate_limiter, schedule_rate_limiter
from src.core.config import settings
from src.database.models.group import Group
from src.database.models.schedule import Schedule
from src.database.models.user import User


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Resets rate limiters before and after each test."""
    log_rate_limiter.reset()
    schedule_rate_limiter.reset()
    yield
    log_rate_limiter.reset()
    schedule_rate_limiter.reset()


@pytest.mark.asyncio
async def test_root_healthcheck():
    """GET / returns plaintext alive message for Render health check."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.text == "Kichobot is alive! 🤖"
        assert "text/plain" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_health_and_bot_health_endpoints(mock_db):
    """GET /health and GET /bot/health return full health monitoring details."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Both /health and /bot/health should respond identically
        for endpoint in ("/health", "/bot/health"):
            response = await client.get(endpoint)
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["healthy"] is True
            assert "checks" in data
            assert data["checks"]["database"] is True
            assert "memory" in data
            assert "rss_mb" in data["memory"]
            assert "uptime" in data


@pytest.mark.asyncio
async def test_health_endpoint_unhealthy_on_db_down():
    """Health check returns 503 and unhealthy status if database is unreachable."""
    with patch("src.api.health.health_monitor.check_database", AsyncMock(return_value=False)):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["healthy"] is False
            assert data["checks"]["database"] is False


@pytest.mark.asyncio
async def test_webhook_secret_token_validation():
    """POST /bot/webhook validates X-Telegram-Bot-Api-Secret-Token."""
    secret = "secret_token_1234567890abcdef"
    with patch.object(settings, "BOT_MODE", "webhook"), \
         patch.object(settings, "WEBHOOK_SECRET_TOKEN", secret):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Missing secret token -> 403 Forbidden
            res_missing = await client.post("/bot/webhook", json={"update_id": 100})
            assert res_missing.status_code == 403

            # 2. Invalid secret token -> 403 Forbidden
            res_wrong = await client.post(
                "/bot/webhook",
                headers={"x-telegram-bot-api-secret-token": "wrong_token"},
                json={"update_id": 100},
            )
            assert res_wrong.status_code == 403

            # 3. Valid secret token -> 200 OK
            res_ok = await client.post(
                "/bot/webhook",
                headers={"x-telegram-bot-api-secret-token": secret},
                json={"update_id": 100},
            )
            assert res_ok.status_code == 200
            assert res_ok.json() == {"ok": True}


@pytest.mark.asyncio
async def test_webhook_dispatches_update():
    """POST /bot/webhook updates activity and feeds update to dispatcher."""
    secret = "valid_secret_test"
    mock_feed = AsyncMock()
    with patch.object(settings, "BOT_MODE", "webhook"), \
         patch.object(settings, "WEBHOOK_SECRET_TOKEN", secret), \
         patch("src.api.app.get_dispatcher") as mock_get_dp:
        mock_dp = MagicMock()
        mock_dp.feed_update = mock_feed
        mock_get_dp.return_value = mock_dp

        initial_activity = health_monitor.last_activity

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/bot/webhook",
                headers={"x-telegram-bot-api-secret-token": secret},
                json={"update_id": 9999, "message": {"message_id": 1, "date": 123456, "chat": {"id": 1, "type": "private"}}},
            )
            assert res.status_code == 200
            assert health_monitor.last_activity >= initial_activity

            # Let background task run
            await asyncio.sleep(0.05)
            assert mock_feed.await_count == 1


@pytest.mark.asyncio
async def test_client_log_endpoint():
    """POST /bot/log handles valid payloads, empty body, oversized body, and rate limits."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Valid log
        res = await client.post("/bot/log", content="Client button click event")
        assert res.status_code == 200
        assert res.json() == "logged"

        # 2. Empty body -> 400
        res_empty = await client.post("/bot/log", content="")
        assert res_empty.status_code == 400
        assert res_empty.json() == {"error": "Empty body"}

        # 3. Oversized payload (> 2048 bytes) -> 413
        large_body = "x" * 2050
        res_large = await client.post("/bot/log", content=large_body)
        assert res_large.status_code == 413
        assert res_large.json() == {"error": "Payload too large (max 2KB)"}

        # 4. Rate limiting (10 requests per minute)
        log_rate_limiter.reset()
        for _ in range(10):
            r = await client.post("/bot/log", content="test")
            assert r.status_code == 200

        # 11th request -> 429 Too Many Requests
        res_rate = await client.post("/bot/log", content="test")
        assert res_rate.status_code == 429
        assert "Слишком много запросов" in res_rate.json()["error"]


@pytest.mark.asyncio
async def test_get_user_schedule_endpoint(mock_db):
    """GET /bot/get_user_schedule handles validation, database lookups, and rate limits."""
    # Create user with group
    group = Group(id=301, name="IS-21", faculty="FIT", course=3)
    await group.insert()

    user = User(userId=5555, group=301, scheduleType="student")
    await user.insert()

    mock_fetch = AsyncMock(return_value={"records": [{"subject": "Databases", "time": "09:00", "dayIndex": 1}], "total": 1})
    with patch("src.api.app.buketov_api_service.fetch_schedule", mock_fetch):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Missing userId -> 400
            res_no_id = await client.get("/bot/get_user_schedule")
            assert res_no_id.status_code == 400

            # 2. Invalid userId -> 400
            res_bad_id = await client.get("/bot/get_user_schedule?userId=not_a_number")
            assert res_bad_id.status_code == 400
            res_neg_id = await client.get("/bot/get_user_schedule?userId=-5")
            assert res_neg_id.status_code == 400

            # 3. User not found -> 200 with null schedules
            res_missing_user = await client.get("/bot/get_user_schedule?userId=999999")
            assert res_missing_user.status_code == 200
            assert res_missing_user.json() == {"studentSchedule": None, "teacherSchedule": None}

            # 4. Existing user -> 200 with schedule data
            res_user = await client.get("/bot/get_user_schedule?userId=5555")
            assert res_user.status_code == 200
            data = res_user.json()
            assert data["scheduleType"] == "student"
            assert len(data["studentSchedule"]["schedule"]) == 6
            assert data["studentSchedule"]["schedule"][0]["day"] == "Понедельник"
            assert len(data["studentSchedule"]["schedule"][0]["subjects"]) == 1


@pytest.mark.asyncio
async def test_get_user_schedule_rate_limiting():
    """GET /bot/get_user_schedule limits to 45 requests per minute."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(45):
            r = await client.get("/bot/get_user_schedule?userId=1")
            assert r.status_code in (200, 400)

        # 46th request -> 429
        r_exceeded = await client.get("/bot/get_user_schedule?userId=1")
        assert r_exceeded.status_code == 429
        assert "Слишком много запросов" in r_exceeded.json()["error"]


@pytest.mark.asyncio
async def test_cron_sync_endpoint():
    """POST /api/cron/sync triggers daily sync when authorized."""
    secret = "cron_secret_test_key"
    with patch.object(settings, "WEBHOOK_SECRET_TOKEN", secret):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Unauthorized (wrong key)
            res_unauth = await client.post("/api/cron/sync?key=wrong_key")
            assert res_unauth.status_code == 403

            # 2. Authorized (via query parameter)
            with patch("src.api.app.run_daily_data_update", AsyncMock()) as mock_daily, \
                 patch("src.api.app.warmup_top_schedules", AsyncMock()) as mock_warmup:
                res_ok = await client.post(f"/api/cron/sync?key={secret}")
                assert res_ok.status_code == 200
                assert res_ok.json() == {"ok": True, "message": "Daily sync triggered"}
                await asyncio.sleep(0.05)
                assert mock_daily.await_count == 1
                assert mock_warmup.await_count == 1

            # 3. Authorized (via header X-Cron-Key)
            with patch("src.api.app.run_daily_data_update", AsyncMock()) as mock_daily, \
                 patch("src.api.app.warmup_top_schedules", AsyncMock()) as mock_warmup:
                res_header = await client.post("/api/cron/sync", headers={"x-cron-key": secret})
                assert res_header.status_code == 200
                await asyncio.sleep(0.05)
                assert mock_daily.await_count == 1
                assert mock_warmup.await_count == 1


@pytest.mark.asyncio
async def test_lifespan_manager(mock_db):
    """Tests the lifespan context manager startup and graceful shutdown."""
    test_app = create_app()
    with patch("src.api.app.init_db", AsyncMock()) as mock_init, \
         patch("src.api.app.close_db", AsyncMock()) as mock_close, \
         patch("src.api.app.start_scheduler") as mock_sched_start, \
         patch("src.api.app.stop_scheduler") as mock_sched_stop, \
         patch("src.api.app.get_bot") as mock_get_bot:
        mock_bot = MagicMock()
        mock_bot.set_my_commands = AsyncMock()
        mock_bot.set_my_description = AsyncMock()
        mock_bot.set_my_short_description = AsyncMock()
        mock_bot.set_webhook = AsyncMock()
        mock_bot.session = MagicMock()
        mock_bot.session.close = AsyncMock()
        mock_get_bot.return_value = mock_bot

        async with lifespan(test_app):
            assert mock_init.await_count == 1
            assert mock_sched_start.call_count == 1
            assert mock_bot.set_my_commands.await_count == 1

        assert mock_sched_stop.call_count == 1
        assert mock_bot.session.close.await_count == 1
        assert mock_close.await_count == 1


@pytest.mark.asyncio
async def test_get_user_schedule_mongodb_fallback(mock_db):
    """GET /bot/get_user_schedule falls back to MongoDB when API is down without serialization errors."""
    from src.database.models.schedule import ScheduleDay, SubjectItem

    group = Group(id=401, name="FIT-22", faculty="FIT", course=1)
    await group.insert()

    user = User(userId=7777, group=401, scheduleType="student")
    await user.insert()

    # Save fallback schedule in MongoDB
    sched = Schedule(
        groupId=401,
        data=[ScheduleDay(day="Понедельник", subjects=[SubjectItem(time="09:00", subject="Math /Лекция/")])],
    )
    await sched.insert()

    # Buketov API throws exception
    with patch("src.api.app.buketov_api_service.fetch_schedule", AsyncMock(side_effect=Exception("API Down"))):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/bot/get_user_schedule?userId=7777")
            assert res.status_code == 200
            data = res.json()
            assert data["studentSchedule"] is not None
            assert data["studentSchedule"]["isNew"] is False
            assert len(data["studentSchedule"]["schedule"]) == 1
            assert data["studentSchedule"]["schedule"][0]["day"] == "Понедельник"


@pytest.mark.asyncio
async def test_get_user_schedule_teacher(mock_db):
    """GET /bot/get_user_schedule supports teacher schedule both via API and MongoDB fallback."""
    from src.database.models.teacher import Teacher
    from src.database.models.teacher_schedule import TeacherSchedule, TeacherScheduleDay, TeacherLessonItem

    teacher = Teacher(id=801, name="Попова Н. В.", department=10)
    await teacher.insert()

    user = User(userId=8888, teacher=801, scheduleType="teacher")
    await user.insert()

    # 1. API path
    mock_api = AsyncMock(return_value={
        "records": [{
            "day": "Понедельник",
            "dayIndex": 1,
            "slot": 1,
            "time": "08:30-09:20",
            "teacher": "Попова Н. В.",
            "group": "CS-21",
            "subject": "Algorithm Theory",
            "type": "Лекция",
            "room": "101",
            "building": "1",
        }],
        "total": 1,
    })
    with patch("src.api.app.buketov_api_service.fetch_teacher_schedule", mock_api):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/bot/get_user_schedule?userId=8888")
            assert res.status_code == 200
            data = res.json()
            assert data["teacherSchedule"] is not None
            assert data["teacherSchedule"]["isNew"] is True
            assert data["teacherSchedule"]["teacher"]["name"] == "Попова Н. В."

    # 2. MongoDB fallback path
    t_sched = TeacherSchedule(
        teacherId=801,
        data=[TeacherScheduleDay(day="Понедельник", groups=[TeacherLessonItem(time="08:30", group="CS-21", subject="Math")])],
    )
    await t_sched.insert()

    with patch("src.api.app.buketov_api_service.fetch_teacher_schedule", AsyncMock(side_effect=Exception("API Down"))):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/bot/get_user_schedule?userId=8888")
            assert res.status_code == 200
            data = res.json()
            assert data["teacherSchedule"] is not None
            assert data["teacherSchedule"]["isNew"] is False
            assert len(data["teacherSchedule"]["schedule"]) == 1
            assert data["teacherSchedule"]["schedule"][0]["day"] == "Понедельник"


@pytest.mark.asyncio
async def test_cron_sync_rejects_when_key_unconfigured():
    """POST /api/cron/sync returns 403 when no expected key is configured."""
    with patch.object(settings, "WEBHOOK_SECRET_TOKEN", None), \
         patch.object(settings, "TG_TOKEN", ""):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/cron/sync")
            assert res.status_code == 403

