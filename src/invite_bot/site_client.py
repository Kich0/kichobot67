"""Outgoing push of join events to the partner site (X-Secret-Key auth, 3 attempts)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger("invite_bot.site_client")

RETRY_DELAYS: tuple[float, ...] = (0.0, 2.0, 5.0)
REQUEST_TIMEOUT = 5.0


async def notify_site(payload: dict[str, Any]) -> bool:
    """POSTs a join event to INVITE_SITE_WEBHOOK_URL.

    Returns True on a 2xx response. Retries on network errors and 5xx only;
    4xx responses are logged and not retried. With an empty URL nothing is sent
    (pull-only mode via GET /api/invite/events).
    """
    url = (settings.INVITE_SITE_WEBHOOK_URL or "").strip()
    if not url:
        return False

    headers = {"Content-Type": "application/json"}
    if settings.INVITE_SITE_SECRET:
        headers["X-Secret-Key"] = settings.INVITE_SITE_SECRET

    tg_id = payload.get("telegram_id")
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as e:
                logger.warning(f"[InviteBot] push attempt {attempt} failed for tg_id={tg_id}: {type(e).__name__}")
                continue

            if 200 <= response.status_code < 300:
                logger.info(f"[InviteBot] push ok tg_id={tg_id} status={response.status_code}")
                return True
            if 400 <= response.status_code < 500:
                logger.warning(f"[InviteBot] push rejected tg_id={tg_id} status={response.status_code}")
                return False
            logger.warning(f"[InviteBot] push attempt {attempt} got status={response.status_code} tg_id={tg_id}")

    logger.error(f"[InviteBot] push gave up after {len(RETRY_DELAYS)} attempts tg_id={tg_id}")
    return False


__all__ = ["notify_site", "RETRY_DELAYS"]
