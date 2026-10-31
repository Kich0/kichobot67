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

DONER_FIRM_ID = "70000001117430885"
DONER_2GIS_URL = f"https://2gis.kz/karaganda/geo/{DONER_FIRM_ID}"
DONER_SHORT_URL = "https://is.gd/doner_na_yuge"
DONER_SHORT_URL_BUTTON = "https://is.gd/doner_na_yuge"
DONER_SHORT_URL_LINK = "https://is.gd/doner_yug_addr"
DONER_DGIS_SCHEME = f"dgis://2gis.ru/firm/{DONER_FIRM_ID}"
DONER_BUTTON_TEXT = "🌯 Открыть Донер на Юге в 2GIS ↗️"


class DonerAdService:
    """Manages sponsor metrics and analytics for «Донер на Юге»."""

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
            short: If True, produces clean compact URL (/r/doner?t=btn&u=...)
        """
        if short:
            if target in ("link", "text_link", "address", "addr"):
                return DONER_SHORT_URL_LINK
            return DONER_SHORT_URL_BUTTON

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

    def generate_bridge_html(self, firm_id: str = DONER_FIRM_ID) -> str:
        """Generates an optimized, branded deep-link bridge HTML page for 2GIS app launch.

        This page seamlessly triggers the 2GIS mobile app on iOS and Android,
        provides an interactive fast-loader with fallback controls, and redirects
        to the 2GIS web version if the app is not installed.
        """
        web_fallback = f"https://2gis.kz/karaganda/geo/{firm_id}"
        dgis_scheme = f"dgis://2gis.ru/firm/{firm_id}"
        android_intent = (
            f"intent://2gis.ru/firm/{firm_id}#Intent;scheme=dgis;"
            f"package=ru.dublgis.dgismobile;S.browser_fallback_url={quote_plus(web_fallback)};end;"
        )

        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Донер на Юге — 2GIS</title>
    <meta name="description" content="Переход в приложение 2GIS: Донер на Юге (Караганда)">
    <meta property="og:title" content="🌯 Донер на Юге — 2GIS">
    <meta property="og:description" content="Сочные донеры, снеки и комбо 24/7! Скидка по промокоду KICHO.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{web_fallback}">
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root {{
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --text-main: #0f172a;
            --text-muted: #64748b;
            --primary: #00a550;
            --primary-hover: #008f45;
            --primary-text: #ffffff;
            --secondary-bg: #f1f5f9;
            --secondary-text: #334155;
            --accent-border: #e2e8f0;
            --badge-bg: #fef3c7;
            --badge-text: #92400e;
            --badge-border: #fde68a;
        }}
        @media (prefers-color-scheme: dark) {{
            :root {{
                --bg-color: #0e1621;
                --card-bg: #17212b;
                --text-main: #f8fafc;
                --text-muted: #94a3b8;
                --primary: #00b859;
                --primary-hover: #009e4c;
                --primary-text: #ffffff;
                --secondary-bg: #242f3d;
                --secondary-text: #cbd5e1;
                --accent-border: #242f3d;
                --badge-bg: #2d2417;
                --badge-text: #fcd34d;
                --badge-border: #78350f;
            }}
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }}
        .card {{
            width: 100%;
            max-width: 400px;
            background-color: var(--card-bg);
            border-radius: 20px;
            border: 1px solid var(--accent-border);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.08);
            padding: 28px 24px;
            text-align: center;
        }}
        .icon-wrap {{
            width: 72px;
            height: 72px;
            margin: 0 auto 16px;
            background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 36px;
            box-shadow: 0 8px 16px rgba(234, 88, 12, 0.25);
        }}
        h1 {{
            font-size: 22px;
            font-weight: 700;
            margin-bottom: 6px;
            letter-spacing: -0.3px;
        }}
        .status {{
            font-size: 14px;
            color: var(--text-muted);
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}
        .spinner {{
            width: 16px;
            height: 16px;
            border: 2px solid var(--text-muted);
            border-top-color: var(--primary);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }}
        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}
        .btn-main {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            width: 100%;
            padding: 14px 20px;
            background-color: var(--primary);
            color: var(--primary-text);
            text-decoration: none;
            font-size: 16px;
            font-weight: 600;
            border-radius: 12px;
            transition: background-color 0.15s ease, transform 0.1s ease;
            box-shadow: 0 4px 12px rgba(0, 165, 80, 0.25);
            margin-bottom: 12px;
        }}
        .btn-main:hover, .btn-main:active {{
            background-color: var(--primary-hover);
            transform: scale(0.99);
        }}
        .btn-secondary {{
            display: block;
            width: 100%;
            padding: 11px 16px;
            background-color: var(--secondary-bg);
            color: var(--secondary-text);
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            border-radius: 10px;
            transition: opacity 0.15s ease;
            margin-bottom: 20px;
        }}
        .btn-secondary:hover {{
            opacity: 0.9;
        }}
        .promo-pill {{
            display: inline-block;
            background-color: var(--badge-bg);
            color: var(--badge-text);
            border: 1px solid var(--badge-border);
            font-size: 12px;
            font-weight: 600;
            padding: 6px 12px;
            border-radius: 9999px;
            letter-spacing: 0.2px;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon-wrap">🌯</div>
        <h1>Донер на Юге</h1>
        <div class="status">
            <span class="spinner" id="spinner"></span>
            <span id="status-text">Переход в приложение 2GIS...</span>
        </div>
        <a id="btn-open-app" href="{dgis_scheme}" class="btn-main">
            <span>🗺️ Открыть в приложении 2GIS</span>
        </a>
        <a id="btn-open-web" href="{web_fallback}" class="btn-secondary">
            <span>🌐 Открыть веб-версию 2GIS</span>
        </a>
        <div class="promo-pill">
            🎁 Скидка по промокоду: <strong>KICHO</strong>
        </div>
    </div>

    <script>
        (function() {{
            var webFallback = "{web_fallback}";
            var dgisScheme = "{dgis_scheme}";
            var androidIntent = "{android_intent}";

            var ua = navigator.userAgent || "";
            var isAndroid = /Android/i.test(ua);
            var isIOS = /iPhone|iPad|iPod/i.test(ua);

            var deepLinkUrl = isAndroid ? androidIntent : (isIOS ? dgisScheme : webFallback);

            var appBtn = document.getElementById("btn-open-app");
            var webBtn = document.getElementById("btn-open-web");
            var statusText = document.getElementById("status-text");
            var spinner = document.getElementById("spinner");

            if (appBtn) {{
                appBtn.href = deepLinkUrl;
            }}
            if (webBtn) {{
                webBtn.href = webFallback;
            }}

            // Telegram WebApp initialization
            if (window.Telegram && window.Telegram.WebApp) {{
                try {{
                    window.Telegram.WebApp.ready();
                    window.Telegram.WebApp.expand();
                }} catch (e) {{}}
            }}

            var fallbackTimer = null;
            function cancelFallback() {{
                if (fallbackTimer) {{
                    clearTimeout(fallbackTimer);
                    fallbackTimer = null;
                }}
            }}

            if (appBtn) {{
                appBtn.addEventListener("click", cancelFallback);
            }}
            if (webBtn) {{
                webBtn.addEventListener("click", cancelFallback);
            }}

            document.addEventListener("visibilitychange", function() {{
                if (document.hidden) {{
                    cancelFallback();
                }}
            }});
            window.addEventListener("pagehide", cancelFallback);
            window.addEventListener("blur", cancelFallback);

            // Inform user if browser security policy prevented instant app launch
            setTimeout(function() {{
                if (!document.hidden && statusText) {{
                    statusText.textContent = "Не открылось автоматически? Нажмите кнопку:";
                    if (spinner) {{
                        spinner.style.display = "none";
                    }}
                }}
            }}, 1500);

            function tryLaunch() {{
                if (isAndroid) {{
                    try {{
                        window.location.href = androidIntent;
                    }} catch (err) {{
                        window.location.href = webFallback;
                    }}
                    fallbackTimer = setTimeout(function() {{
                        if (!document.hidden) {{
                            window.location.href = webFallback;
                        }}
                    }}, 2500);
                }} else if (isIOS) {{
                    window.location.href = dgisScheme;
                    fallbackTimer = setTimeout(function() {{
                        if (!document.hidden) {{
                            window.location.href = webFallback;
                        }}
                    }}, 2500);
                }} else {{
                    if (statusText) {{
                        statusText.textContent = "Переход на страницу 2GIS...";
                    }}
                    fallbackTimer = setTimeout(function() {{
                        window.location.href = webFallback;
                    }}, 1000);
                }}
            }}

            tryLaunch();
        }})();
    </script>
</body>
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
                logger.warning(f"Failed to record Doner impression for user {user_id}: {exc!r}")

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
                if uid == 0 and self._last_viewer_uid > 0:
                    uid = self._last_viewer_uid
                resolved_source = source
                if resolved_source in ("button", "text_link", "schedule", "") and self._last_viewer_source:
                    resolved_source = self._last_viewer_source
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
                    source=resolved_source,
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
                logger.warning(f"Failed to record Doner click for user {user_id}: {exc!r}")

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
