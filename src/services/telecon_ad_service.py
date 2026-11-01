"""Service for tracking and analyzing advertising metrics for «Телеконовая долина».

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
from src.database.models.telecon_ad_event import TeleconAdEvent
from src.database.models.user_action import UserAction

logger = get_logger("telecon_ad_service")

TELECON_FIRM_ID = "70000001083364853"
TELECON_2GIS_URL = f"https://2gis.kz/karaganda/firm/{TELECON_FIRM_ID}"
TELECON_SHORT_URL = f"https://2gis.kz/karaganda/firm/{TELECON_FIRM_ID}"
TELECON_SHORT_URL_BUTTON = f"https://2gis.kz/karaganda/firm/{TELECON_FIRM_ID}"
TELECON_SHORT_URL_LINK = f"https://2gis.kz/karaganda/firm/{TELECON_FIRM_ID}"
TELECON_DGIS_SCHEME = f"dgis://2gis.ru/firm/{TELECON_FIRM_ID}"
TELECON_BUTTON_TEXT = "👉 Подробнее"


class TeleconAdService:
    """Manages partner metrics and analytics for «Телеконовая долина»."""

    def __init__(self) -> None:
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._last_viewer_uid: int = 0
        self._last_viewer_source: str = ""

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
        short: bool = False,
    ) -> str:
        """Returns tracking redirect URL if domain is configured, otherwise fallback to direct 2GIS URL.

        Args:
            target: 'button' or 'link'
            user_id: Optional Telegram viewer user ID
            source: Placement source ('student_schedule', 'teacher_schedule', etc.)
            base_url: Optional explicit base URL override
            short: If True, produces clean compact URL (/r/telecon?t=btn&u=...)
        """
        domain = base_url or settings.WEBHOOK_DOMAIN
        if domain:
            clean_domain = str(domain).rstrip("/")
            if not clean_domain.startswith(("http://", "https://")):
                clean_domain = f"https://{clean_domain}"
            uid_param = int(user_id) if user_id and str(user_id).isdigit() else 0

            target_code = "btn" if target in ("button", "btn") else "link"
            url = f"{clean_domain}/r/telecon?t={target_code}&u={uid_param}"
            if source:
                src_code = "std" if "student" in str(source) else ("tch" if "teacher" in str(source) else str(source))
                url += f"&s={quote_plus(src_code)}"
            return url

        if short:
            return TELECON_SHORT_URL
        return TELECON_2GIS_URL

    def get_short_tracking_url(
        self,
        target: str = "button",
        user_id: int | str | None = None,
        source: str | None = None,
        base_url: str | None = None,
    ) -> str:
        """Returns clean compact short tracking redirect URL."""
        return self.get_tracking_url(
            target=target,
            user_id=user_id,
            source=source,
            base_url=base_url,
            short=True,
        )

    def generate_bridge_html(self, firm_id: str = TELECON_FIRM_ID) -> str:
        """Generates an ultra-fast, pitch-black deep-link bridge HTML page for 2GIS app launch.

        Zero visible DOM elements, pure #000000 black screen (no cards, buttons, spinners or text),
        and immediate location.replace launch of 2GIS mobile app via Android intent / iOS scheme.
        """
        web_fallback = f"https://2gis.kz/karaganda/firm/{firm_id}"
        dgis_scheme = f"dgis://2gis.ru/firm/{firm_id}"
        android_intent = (
            f"intent://2gis.ru/firm/{firm_id}#Intent;scheme=dgis;"
            f"package=ru.dublgis.dgismobile;S.browser_fallback_url={quote_plus(web_fallback)};end;"
        )

        return f"""<!DOCTYPE html>
<html lang="ru" style="background:#000;background-color:#000;">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
    <title>2GIS</title>
    <style>
        html, body {{
            background: #000000 !important;
            background-color: #000000 !important;
            color: #000000 !important;
            margin: 0;
            padding: 0;
            width: 100vw;
            height: 100vh;
            overflow: hidden;
            border: none;
            outline: none;
        }}
    </style>
    <script>
        (function() {{
            var web = "{web_fallback}";
            var dgis = "{dgis_scheme}";
            var intent = "{android_intent}";
            var ua = navigator.userAgent || "";
            var isAndroid = /Android/i.test(ua);
            var isIOS = /iPhone|iPad|iPod/i.test(ua);

            if (isAndroid) {{
                window.location.replace(intent);
            }} else if (isIOS) {{
                window.location.replace(dgis);
            }} else {{
                window.location.replace(web);
            }}

            setTimeout(function() {{
                window.location.replace(web);
            }}, 1200);
        }})();
    </script>
</head>
<body style="background:#000;background-color:#000;margin:0;padding:0;"></body>
</html>"""

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
        if str(user_id).isdigit() and int(user_id) > 0:
            self._last_viewer_uid = int(user_id)
            if source:
                self._last_viewer_source = source

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

                event = TeleconAdEvent(
                    userId=uid,
                    username=resolved_user,
                    firstName=resolved_fn,
                    lastName=resolved_ln,
                    eventType="impression",
                    source=source,
                    targetUrl=TELECON_2GIS_URL,
                    buttonText=TELECON_BUTTON_TEXT,
                    details=details,
                )
                await event.insert()
            except Exception as exc:
                logger.warning(f"Failed to record Telecon impression for user {user_id}: {exc!r}")

        self._spawn_bg_task(_do_record())

    async def record_click(
        self,
        user_id: int | str | None = None,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        event_type: str = "button_click",
        source: str = "button",
        target_url: str = TELECON_2GIS_URL,
        ip: str | None = None,
        user_agent: str | None = None,
        details: str | None = None,
    ) -> None:
        """Records a button or link click asynchronously in both TeleconAdEvent and UserAction."""
        async def _do_record() -> None:
            try:
                uid = 0
                if user_id is not None and str(user_id).isdigit() and int(user_id) > 0:
                    uid = int(user_id)
                elif self._last_viewer_uid > 0:
                    uid = self._last_viewer_uid

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

                button_label = (
                    TELECON_BUTTON_TEXT if event_type == "button_click" else "📍 Адрес"
                )

                event = TeleconAdEvent(
                    userId=uid,
                    username=resolved_user,
                    firstName=resolved_fn,
                    lastName=resolved_ln,
                    eventType=event_type,
                    source=source,
                    targetUrl=target_url or TELECON_2GIS_URL,
                    buttonText=button_label,
                    ip=ip,
                    userAgent=user_agent,
                    details=details,
                )
                await event.insert()

                # Also register UserAction for overall bot audit trail
                if uid > 0:
                    action_text = (
                        f"Клик по кнопке «{button_label}» (Телеконовая долина)"
                        if event_type == "button_click"
                        else "Переход по ссылке «Адрес» (Телеконовая долина)"
                    )
                    action = UserAction(
                        userId=uid,
                        username=resolved_user,
                        action="ad_click_telecon",
                        text=action_text,
                        metadata={
                            "eventType": event_type,
                            "source": source,
                            "targetUrl": target_url,
                            "ip": ip,
                        },
                    )
                    await action.insert()
            except Exception as exc:
                logger.warning(f"Failed to record Telecon click for user {user_id}: {exc!r}")

        self._spawn_bg_task(_do_record())

    async def get_analytics_summary(self) -> dict[str, Any]:
        """Calculates comprehensive KPIs and breakdowns for administrator /boost command."""
        try:
            all_events = await TeleconAdEvent.find_all().to_list()
        except Exception as exc:
            logger.error(f"Error querying TeleconAdEvent: {exc}")
            all_events = []

        total_impressions = 0
        impression_viewers: set[int] = set()

        button_clicks = 0
        button_clickers: set[int] = set()

        link_clicks = 0
        link_clickers: set[int] = set()

        source_breakdown: dict[str, int] = {}
        impressions_by_source: dict[str, int] = {}
        clicks_by_source: dict[str, int] = {}
        button_clicks_by_source: dict[str, int] = {}
        link_clicks_by_source: dict[str, int] = {}

        now_kz = datetime.now(TIMEZONE)
        today_start_kz = datetime.combine(now_kz.date(), time.min, tzinfo=TIMEZONE)
        today_impressions = 0
        today_button_clicks = 0
        today_link_clicks = 0

        click_events: list[TeleconAdEvent] = []

        for ev in all_events:
            ev_type = ev.event_type
            ev_src = ev.source or "unknown"
            source_breakdown[ev_src] = source_breakdown.get(ev_src, 0) + 1

            ev_dt = ev.created_at
            if ev_dt:
                if ev_dt.tzinfo is None:
                    ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                ev_dt_kz = ev_dt.astimezone(TIMEZONE)
            else:
                ev_dt_kz = now_kz

            is_today = ev_dt_kz >= today_start_kz

            if ev_type == "impression":
                total_impressions += 1
                impressions_by_source[ev_src] = impressions_by_source.get(ev_src, 0) + 1
                if ev.user_id > 0:
                    impression_viewers.add(ev.user_id)
                if is_today:
                    today_impressions += 1

            elif ev_type == "button_click":
                button_clicks += 1
                button_clicks_by_source[ev_src] = button_clicks_by_source.get(ev_src, 0) + 1
                clicks_by_source[ev_src] = clicks_by_source.get(ev_src, 0) + 1
                if ev.user_id > 0:
                    button_clickers.add(ev.user_id)
                if is_today:
                    today_button_clicks += 1
                click_events.append(ev)

            elif ev_type == "link_click":
                link_clicks += 1
                link_clicks_by_source[ev_src] = link_clicks_by_source.get(ev_src, 0) + 1
                clicks_by_source[ev_src] = clicks_by_source.get(ev_src, 0) + 1
                if ev.user_id > 0:
                    link_clickers.add(ev.user_id)
                if is_today:
                    today_link_clicks += 1
                click_events.append(ev)

        total_clicks = button_clicks + link_clicks
        total_unique_clickers = len(button_clickers | link_clickers)
        ctr = (total_clicks / total_impressions * 100.0) if total_impressions > 0 else 0.0

        click_events.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
        recent_clicks = click_events[:10]

        return {
            "total_impressions": total_impressions,
            "unique_viewers": len(impression_viewers),
            "button_clicks": button_clicks,
            "unique_button_clickers": len(button_clickers),
            "link_clicks": link_clicks,
            "unique_link_clickers": len(link_clickers),
            "total_clicks": total_clicks,
            "unique_clickers": total_unique_clickers,
            "ctr": ctr,
            "today_stats": {
                "impressions": today_impressions,
                "button_clicks": today_button_clicks,
                "link_clicks": today_link_clicks,
                "total_clicks": today_button_clicks + today_link_clicks,
            },
            "source_breakdown": source_breakdown,
            "impressions_by_source": impressions_by_source,
            "clicks_by_source": clicks_by_source,
            "button_clicks_by_source": button_clicks_by_source,
            "link_clicks_by_source": link_clicks_by_source,
            "recent_events": recent_clicks,
        }


# Singleton service instance
telecon_ad_service = TeleconAdService()
