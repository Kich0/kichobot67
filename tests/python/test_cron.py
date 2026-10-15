"""Unit and integration tests for Phase 6 Cron Jobs and APScheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import zoneinfo

from src.core.constants import TIMEZONE_NAME
from src.cron.cache_warmup import setup_cache_warmup, warmup_top_schedules
from src.cron.daily_stats import get_kz_start_of_day, log_daily_user_statistics, setup_daily_stats
from src.cron.daily_update import run_daily_data_update, setup_daily_update
from src.cron.scheduler import get_scheduler, start_scheduler, stop_scheduler
from src.cron import setup_all_crons
from src.database.models.group import Group
from src.database.models.user import User
from src.database.models.user_action import UserAction
from src.database.models.user_activity import UserActivity
from src.database.models.user_registration_stat import UserRegistrationStat


@pytest.mark.asyncio
async def test_scheduler_lifecycle():
    """Tests scheduler singleton, starting, and stopping."""
    sched = get_scheduler()
    assert isinstance(sched, AsyncIOScheduler)
    assert sched.timezone == zoneinfo.ZoneInfo(TIMEZONE_NAME)

    start_scheduler()
    assert sched.running is True

    stop_scheduler()
    await asyncio.sleep(0.05)
    assert sched.running is False


@pytest.mark.asyncio
async def test_cron_registration():
    """Verifies that setup_all_crons registers the three required jobs with correct timings."""
    sched = AsyncIOScheduler(timezone=zoneinfo.ZoneInfo(TIMEZONE_NAME))
    setup_all_crons(sched)

    job_ids = [job.id for job in sched.get_jobs()]
    assert "daily_data_update" in job_ids
    assert "cache_warmup" in job_ids
    assert "daily_user_statistics" in job_ids

    # Verify triggers
    daily_job = sched.get_job("daily_data_update")
    warmup_job = sched.get_job("cache_warmup")
    stats_job = sched.get_job("daily_user_statistics")

    assert str(daily_job.trigger) == "cron[hour='6', minute='0']"
    assert str(warmup_job.trigger) == "cron[hour='7', minute='30']"
    assert str(stats_job.trigger) == "cron[hour='23', minute='59']"


@pytest.mark.asyncio
async def test_run_daily_data_update_no_auto_auth():
    """Tests daily update when AUTO_KSU_AUTH is False."""
    with patch("src.cron.daily_update.settings.AUTO_KSU_AUTH", False):
        # Should complete cleanly without error
        await run_daily_data_update(is_startup=False)
        await run_daily_data_update(is_startup=True)


@pytest.mark.asyncio
async def test_warmup_top_schedules(mock_db):
    """Tests morning schedule cache warmup on top active student groups."""
    # Create groups
    g1 = Group(id=101, name="CS-21", faculty="FIT", course=2)
    g2 = Group(id=102, name="CS-22", faculty="FIT", course=2)
    await g1.insert()
    await g2.insert()

    # Create users belonging to groups
    await User(userId=1001, group=101).insert()
    await User(userId=1002, group=101).insert()
    await User(userId=1003, group=102).insert()

    mock_fetch = AsyncMock(return_value={"records": [{"subject": "Math"}], "total": 1})
    with patch("src.cron.cache_warmup.buketov_api_service.fetch_schedule", mock_fetch):
        count = await warmup_top_schedules(limit=5)
        assert count == 2
        assert mock_fetch.await_count == 2


@pytest.mark.asyncio
async def test_log_daily_user_statistics(mock_db):
    """Tests 23:59 daily statistics calculation and persistence."""
    start_utc = get_kz_start_of_day()

    # Create user actions today
    await UserAction(userId=201, action="test", text="action 1", createdAt=start_utc).insert()
    await UserAction(userId=201, action="test", text="action 2", createdAt=start_utc).insert()
    await UserAction(userId=202, action="test", text="action 3", createdAt=start_utc).insert()

    # Create registered users today
    await User(userId=201, createdAt=start_utc).insert()
    await User(userId=202, createdAt=start_utc).insert()

    res = await log_daily_user_statistics()
    assert res["active_users"] == 2
    assert res["registered_users"] == 2

    # Check database records
    activities = await UserActivity.find().to_list()
    assert len(activities) == 1
    assert activities[0].user_activity == 2

    registrations = await UserRegistrationStat.find().to_list()
    assert len(registrations) == 1
    assert registrations[0].registered_users == 2


def test_get_kz_start_of_day():
    """Tests timezone-aware start of day computation."""
    start_dt = get_kz_start_of_day()
    assert start_dt.tzinfo == timezone.utc
    # In KZ (UTC+5), 00:00:00 KZ is 19:00:00 UTC of previous day
    kz_tz = zoneinfo.ZoneInfo("Asia/Almaty")
    in_kz = start_dt.astimezone(kz_tz)
    assert in_kz.hour == 0
    assert in_kz.minute == 0
    assert in_kz.second == 0
    assert in_kz.microsecond == 0


@pytest.mark.asyncio
async def test_warmup_top_schedules_fallback_to_groups(mock_db):
    """Tests that when users have fewer groups than limit, warmup queries extra groups from Group collection."""
    # 3 groups in total in database
    g1 = Group(id=301, name="IS-1")
    g2 = Group(id=302, name="IS-2")
    g3 = Group(id=303, name="IS-3")
    await g1.insert()
    await g2.insert()
    await g3.insert()

    # Only 1 group has an active user
    await User(userId=9001, group=301).insert()

    mock_fetch = AsyncMock(return_value={"records": [{"subject": "Networks"}], "total": 1})
    with patch("src.cron.cache_warmup.buketov_api_service.fetch_schedule", mock_fetch):
        # limit is 3, so 1 comes from User aggregate, 2 come from Group NotIn fallback
        warmed = await warmup_top_schedules(limit=3)
        assert warmed == 3
        assert mock_fetch.await_count == 3


@pytest.mark.asyncio
async def test_daily_stats_query_structure(mock_db):
    """Verifies that Beanie filter query preserves $or and $gte operators."""
    from beanie.operators import GTE, Or

    start_utc = get_kz_start_of_day()
    start_naive = start_utc.replace(tzinfo=None)

    q = User.find(
        Or(
            GTE(User.created_at, start_utc),
            GTE(User.created_at, start_naive),
        )
    )
    filter_dict = q.get_filter_query()
    assert "$or" in filter_dict
    assert len(filter_dict["$or"]) == 2
    assert "$gte" in filter_dict["$or"][0]["createdAt"]

