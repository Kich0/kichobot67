"""Service for tracking and analyzing advertising metrics for «Донер на Юге».

Provides non-blocking async event recording for impressions, button clicks,
and link clicks, plus comprehensive analytics aggregation for the /boost admin command
and Google Drive exports.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from typing import Any
from urllib.parse import quote_plus

from src.core.config import settings
from src.core.constants import TIMEZONE
from src.core.logging import get_logger
from src.database.models.doner_ad_event import DonerAdEvent
from src.database.models.user_action import UserAction

logger = get_logger("doner_ad_service")

DONER_2GIS_URL = "https://2gis.kz/karaganda/geo/70000001117430885"
DONER_BUTTON_TEXT = "🌯 Открыть Донер на Юге в 2GIS ↗️"


class DonerAdService:
    """Manages sponsor metrics and analytics for «Донер на Юге»."""

    def __init__(self) -> None:
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    def _spawn_bg_task(self, coro: Any) -> asyncio.Task[Any]:
        """Runs a coroutine in background while maintaining strong reference to avoid GC collection."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def get_tracking_url(
        self,
        target: str = "button",
        user_id: int | str | None = None,
        source: str | None = None,
        base_url: str | None = None,
    ) -> str:
        """Returns tracking redirect URL if domain is configured, otherwise fallback to direct 2GIS URL.

        Args:
            target: 'button' or 'link'
            user_id: Optional Telegram viewer user ID
            source: Placement source ('student_schedule', 'teacher_schedule', etc.)
            base_url: Optional explicit base URL override
        """
        domain = base_url or settings.WEBHOOK_DOMAIN
        if domain:
            clean_domain = str(domain).rstrip("/")
            if not clean_domain.startswith(("http://", "https://")):
                clean_domain = f"https://{clean_domain}"
            uid_param = int(user_id) if user_id and str(user_id).isdigit() else 0
            url = f"{clean_domain}/ad/doner/click?target={target}&uid={uid_param}"
            if source:
                url += f"&source={quote_plus(str(source))}"
            return url
        return DONER_2GIS_URL

    async def record_impression(
        self,
        user_id: int | str | None,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        source: str = "student_schedule",
        details: str | None = None,
    ) -> None:
        """Records an ad impression asynchronously (fire-and-forget)."""
        if not user_id:
            return

        async def _do_record() -> None:
            try:
                uid = int(user_id) if str(user_id).isdigit() else 0
                resolved_user = username
                resolved_fn = first_name
                resolved_ln = last_name

                if uid > 0 and (not resolved_user or not resolved_fn):
                    try:
                        from src.database.models.user import User
                        u_doc = await User.find_one({"userId": uid})
                        if u_doc:
                            resolved_user = resolved_user or u_doc.username
                            resolved_fn = resolved_fn or u_doc.first_name
                            resolved_ln = resolved_ln or u_doc.last_name
                    except Exception:
                        pass

                event = DonerAdEvent(
                    userId=uid,
                    username=resolved_user,
                    firstName=resolved_fn,
                    lastName=resolved_ln,
                    eventType="impression",
                    source=source,
                    targetUrl=DONER_2GIS_URL,
                    buttonText=DONER_BUTTON_TEXT,
                    details=details,
                    createdAt=datetime.now(timezone.utc),
                )
                await event.insert()
            except Exception as exc:
                logger.warning(f"Failed to record Doner impression for user {user_id}: {exc}")

        self._spawn_bg_task(_do_record())

    async def record_click(
        self,
        user_id: int | str | None,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        event_type: str = "button_click",
        source: str = "button",
        target_url: str = DONER_2GIS_URL,
        button_text: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        details: str | None = None,
    ) -> None:
        """Records a button or link click asynchronously in both DonerAdEvent and UserAction."""
        async def _do_record() -> None:
            try:
                uid = int(user_id) if user_id and str(user_id).isdigit() else 0
                now_utc = datetime.now(timezone.utc)

                resolved_user = username
                resolved_fn = first_name
                resolved_ln = last_name

                if uid > 0 and (not resolved_user or not resolved_fn):
                    try:
                        from src.database.models.user import User
                        u_doc = await User.find_one({"userId": uid})
                        if u_doc:
                            resolved_user = resolved_user or u_doc.username
                            resolved_fn = resolved_fn or u_doc.first_name
                            resolved_ln = resolved_ln or u_doc.last_name
                    except Exception:
                        pass

                clean_btn_text = button_text or (
                    DONER_BUTTON_TEXT if event_type == "button_click" else "📍 Адрес"
                )

                # 1. Dedicated sponsor collection
                event = DonerAdEvent(
                    userId=uid,
                    username=resolved_user,
                    firstName=resolved_fn,
                    lastName=resolved_ln,
                    eventType=event_type,
                    source=source,
                    targetUrl=target_url or DONER_2GIS_URL,
                    buttonText=clean_btn_text,
                    ip=ip,
                    userAgent=user_agent,
                    details=details,
                    createdAt=now_utc,
                )
                await event.insert()

                # 2. General user audit log
                if uid > 0:
                    action_tag = "ad_click_button" if event_type == "button_click" else "ad_click_link"
                    action_label = (
                        "Клик по кнопке «Донер на Юге» (2GIS)"
                        if event_type == "button_click"
                        else "Переход по ссылке «Донер на Юге» (2GIS)"
                    )
                    user_action = UserAction(
                        userId=uid,
                        username=resolved_user,
                        action=action_tag,
                        text=action_label,
                        createdAt=now_utc,
                    )
                    await user_action.insert()
            except Exception as exc:
                logger.warning(f"Failed to record Doner click for user {user_id}: {exc}")

        self._spawn_bg_task(_do_record())

    async def get_analytics_summary(self) -> dict[str, Any]:
        """Computes comprehensive metrics and summary for «Донер на Юге».

        Returns aggregated statistics including impressions, clicks, unique users,
        daily activity, and recent events.
        """
        try:
            all_events = await DonerAdEvent.find_all().to_list()
        except Exception as exc:
            logger.error(f"Error querying DonerAdEvent: {exc}")
            all_events = []

        total_impressions = 0
        impression_users: set[int] = set()

        button_clicks = 0
        button_users: set[int] = set()

        link_clicks = 0
        link_users: set[int] = set()

        source_breakdown: dict[str, int] = {}
        impressions_by_source: dict[str, int] = {}
        clicks_by_source: dict[str, int] = {}
        button_clicks_by_source: dict[str, int] = {}
        link_clicks_by_source: dict[str, int] = {}

        daily_breakdown: dict[str, dict[str, int]] = {}

        now_kz = datetime.now(TIMEZONE)
        today_str = now_kz.strftime("%Y-%m-%d")

        today_impressions = 0
        today_button_clicks = 0
        today_link_clicks = 0

        click_events: list[DonerAdEvent] = []

        for ev in all_events:
            ev_type = ev.event_type or ""
            u_id = ev.user_id or 0
            src = ev.source or "unknown"
            source_breakdown[src] = source_breakdown.get(src, 0) + 1

            # Determine local date (Asia/Almaty UTC+5)
            ev_dt = ev.created_at or datetime.now(timezone.utc)
            if ev_dt.tzinfo is None:
                ev_dt = ev_dt.replace(tzinfo=timezone.utc)
            kz_date_str = ev_dt.astimezone(TIMEZONE).strftime("%Y-%m-%d")

            if kz_date_str not in daily_breakdown:
                daily_breakdown[kz_date_str] = {
                    "impressions": 0,
                    "button_clicks": 0,
                    "link_clicks": 0,
                    "total_clicks": 0,
                }

            is_today = (kz_date_str == today_str)

            if ev_type == "impression":
                total_impressions += 1
                if u_id > 0:
                    impression_users.add(u_id)
                impressions_by_source[src] = impressions_by_source.get(src, 0) + 1
                daily_breakdown[kz_date_str]["impressions"] += 1
                if is_today:
                    today_impressions += 1

            elif ev_type == "button_click":
                button_clicks += 1
                if u_id > 0:
                    button_users.add(u_id)
                clicks_by_source[src] = clicks_by_source.get(src, 0) + 1
                button_clicks_by_source[src] = button_clicks_by_source.get(src, 0) + 1
                daily_breakdown[kz_date_str]["button_clicks"] += 1
                daily_breakdown[kz_date_str]["total_clicks"] += 1
                click_events.append(ev)
                if is_today:
                    today_button_clicks += 1

            elif ev_type == "link_click":
                link_clicks += 1
                if u_id > 0:
                    link_users.add(u_id)
                clicks_by_source[src] = clicks_by_source.get(src, 0) + 1
                link_clicks_by_source[src] = link_clicks_by_source.get(src, 0) + 1
                daily_breakdown[kz_date_str]["link_clicks"] += 1
                daily_breakdown[kz_date_str]["total_clicks"] += 1
                click_events.append(ev)
                if is_today:
                    today_link_clicks += 1

            else:
                # Any other click or custom interaction
                if "click" in ev_type.lower():
                    button_clicks += 1
                    if u_id > 0:
                        button_users.add(u_id)
                    clicks_by_source[src] = clicks_by_source.get(src, 0) + 1
                    button_clicks_by_source[src] = button_clicks_by_source.get(src, 0) + 1
                    click_events.append(ev)

        total_clicks = button_clicks + link_clicks
        unique_clickers = len(button_users | link_users)
        ctr = (total_clicks / total_impressions * 100.0) if total_impressions > 0 else 0.0
        today_total_clicks = today_button_clicks + today_link_clicks
        today_ctr = (today_total_clicks / today_impressions * 100.0) if today_impressions > 0 else 0.0

        # Sort click events descending by date for recent events list
        click_events.sort(
            key=lambda e: e.created_at if e.created_at else datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        recent_events = click_events[:10]

        return {
            "total_impressions": total_impressions,
            "unique_viewers": len(impression_users),
            "button_clicks": button_clicks,
            "unique_button_clickers": len(button_users),
            "link_clicks": link_clicks,
            "unique_link_clickers": len(link_users),
            "total_clicks": total_clicks,
            "unique_clickers": unique_clickers,
            "ctr": ctr,
            "source_breakdown": source_breakdown,
            "impressions_by_source": impressions_by_source,
            "clicks_by_source": clicks_by_source,
            "button_clicks_by_source": button_clicks_by_source,
            "link_clicks_by_source": link_clicks_by_source,
            "daily_breakdown": daily_breakdown,
            "today_stats": {
                "impressions": today_impressions,
                "button_clicks": today_button_clicks,
                "link_clicks": today_link_clicks,
                "total_clicks": today_total_clicks,
                "ctr": today_ctr,
            },
            "recent_events": recent_events,
            "total_events": len(all_events),
        }


# Global singleton instance
doner_ad_service = DonerAdService()
