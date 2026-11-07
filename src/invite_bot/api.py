"""HTTP endpoints of the invite bot.

- POST /api/invite/link            partner -> us, X-API-Key, returns a one-time invite link
- GET  /api/invite/events          partner -> us, X-API-Key, pull buffered join events
- GET  /api/invite/avatar/{id}     signed (?sig=) avatar proxy, bot token never leaves the server
- POST {INVITE_WEBHOOK_PATH}       Telegram -> us, own secret token, chat_member updates only
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta
import io
import secrets
from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import Update
from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from src.api.rate_limiter import InMemoryRateLimiter
from src.core.config import settings
from src.core.constants import TIMEZONE
from src.core.logging import get_logger
from src.invite_bot.events import get_events, get_link_status, register_link, verify_file_id_signature
from src.invite_bot.instance import get_invite_bot, get_invite_dispatcher

logger = get_logger("invite_bot.api")

router = APIRouter(tags=["invite-bot"])

link_rate_limiter = InMemoryRateLimiter(max_requests=60, window_seconds=60.0)
events_rate_limiter = InMemoryRateLimiter(max_requests=120, window_seconds=60.0)
avatar_rate_limiter = InMemoryRateLimiter(max_requests=120, window_seconds=60.0)

AVATAR_CACHE_SIZE = 50
_avatar_cache: OrderedDict[str, bytes] = OrderedDict()

_background_tasks: set[asyncio.Task[Any]] = set()


def _error(status: int, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, **extra})


def _client_ip(request: Request) -> str:
    # Lazy import: src.api.app imports this module at load time.
    from src.api.app import get_client_ip

    return get_client_ip(request)


def _api_key_valid(provided: str | None) -> bool:
    expected = settings.INVITE_API_KEY
    return bool(expected and provided and secrets.compare_digest(provided, expected))


def reset_invite_api_state() -> None:
    """Clears rate limiters and avatar cache (used by tests)."""
    link_rate_limiter.reset()
    events_rate_limiter.reset()
    avatar_rate_limiter.reset()
    _avatar_cache.clear()


async def _read_requested_chat_id(request: Request) -> tuple[int | None, bool]:
    """Returns (chat_id, is_valid). Missing/empty body -> (None, True)."""
    try:
        raw = await request.body()
    except Exception:
        return None, True
    if not raw or not raw.strip():
        return None, True
    try:
        import orjson

        data = orjson.loads(raw)
    except Exception:
        return None, False
    if not isinstance(data, dict) or data.get("chat_id") in (None, ""):
        return None, isinstance(data, dict)
    try:
        return int(data["chat_id"]), True
    except (TypeError, ValueError):
        return None, False


@router.post("/api/invite/link")
async def create_invite_link(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    if not settings.invite_enabled:
        return _error(503, "Invite bot disabled")
    if not _api_key_valid(x_api_key):
        logger.warning(f"[InviteBot] unauthorized /api/invite/link from {_client_ip(request)}")
        return _error(401, "Unauthorized")
    if not link_rate_limiter.is_allowed(_client_ip(request)):
        return _error(429, "Too Many Requests")

    requested, valid = await _read_requested_chat_id(request)
    if not valid:
        return _error(400, "Invalid request body")
    allowed = settings.invite_chat_ids
    if requested is not None and requested not in allowed:
        return _error(400, "chat_id not allowed")
    chat_id = requested if requested is not None else allowed[0]

    expire: datetime | None = None
    if settings.INVITE_LINK_EXPIRE_HOURS > 0:
        expire = datetime.now(TIMEZONE) + timedelta(hours=settings.INVITE_LINK_EXPIRE_HOURS)

    bot = get_invite_bot()
    if bot is None:
        return _error(503, "Invite bot disabled")

    try:
        link = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
            expire_date=expire,
        )
    except TelegramRetryAfter as e:
        return JSONResponse(
            status_code=429,
            content={"error": "Too Many Requests", "retry_after": e.retry_after},
            headers={"Retry-After": str(e.retry_after)},
        )
    except TelegramAPIError as e:
        logger.error(f"[InviteBot] createChatInviteLink failed for chat {chat_id}: {e}")
        return _error(502, "Telegram error", detail=str(e))

    expire_iso = expire.isoformat() if expire else None
    register_link(link.invite_link, chat_id=chat_id, expire_date=expire_iso)

    return JSONResponse(
        status_code=200,
        content={
            "invite_link": link.invite_link,
            "chat_id": chat_id,
            "status": "active",
            "member_limit": 1,
            "expire_date": expire_iso,
        },
    )


@router.get("/api/invite/link/status")
async def check_link_status(
    request: Request,
    invite_link: str = Query(..., description="Full invite link URL, e.g. https://t.me/+..."),
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    if not settings.invite_enabled:
        return _error(503, "Invite bot disabled")
    if not _api_key_valid(x_api_key):
        return _error(401, "Unauthorized")
    if not events_rate_limiter.is_allowed(_client_ip(request)):
        return _error(429, "Too Many Requests")

    status_info = get_link_status(invite_link.strip())
    if status_info is None:
        return _error(404, "Invite link not found")
    return JSONResponse(status_code=200, content=status_info)


@router.get("/api/invite/events")
async def list_join_events(
    request: Request,
    since: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    if not settings.invite_enabled:
        return _error(503, "Invite bot disabled")
    if not _api_key_valid(x_api_key):
        return _error(401, "Unauthorized")
    if not events_rate_limiter.is_allowed(_client_ip(request)):
        return _error(429, "Too Many Requests")
    return JSONResponse(status_code=200, content={"events": get_events(since)})


@router.get("/api/invite/avatar/{file_id}")
async def get_avatar(
    request: Request,
    file_id: str,
    sig: str | None = Query(default=None),
) -> Response:
    if not settings.invite_enabled:
        return _error(503, "Invite bot disabled")
    if not verify_file_id_signature(file_id, sig):
        return _error(403, "Forbidden")
    if not avatar_rate_limiter.is_allowed(_client_ip(request)):
        return _error(429, "Too Many Requests")

    headers = {"Cache-Control": "public, max-age=86400"}
    cached = _avatar_cache.get(file_id)
    if cached is not None:
        _avatar_cache.move_to_end(file_id)
        return Response(content=cached, media_type="image/jpeg", headers=headers)

    bot = get_invite_bot()
    if bot is None:
        return _error(503, "Invite bot disabled")
    try:
        tg_file = await bot.get_file(file_id)
        buffer = io.BytesIO()
        await bot.download_file(tg_file.file_path, destination=buffer)
        content = buffer.getvalue()
    except Exception as e:
        logger.warning(f"[InviteBot] avatar download failed: {type(e).__name__}")
        return _error(404, "Avatar not found")

    _avatar_cache[file_id] = content
    while len(_avatar_cache) > AVATAR_CACHE_SIZE:
        _avatar_cache.popitem(last=False)
    return Response(content=content, media_type="image/jpeg", headers=headers)


@router.post(settings.INVITE_WEBHOOK_PATH)
async def process_invite_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    bot = get_invite_bot()
    if bot is None:
        return _error(503, "Invite bot disabled")

    expected = settings.invite_webhook_secret
    token = x_telegram_bot_api_secret_token
    if not expected or not token or not secrets.compare_digest(token, expected):
        logger.warning(f"[InviteBot] unauthorized webhook attempt from {_client_ip(request)}")
        return _error(403, "Forbidden")

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=200, content={"ok": True})
    if not payload or not isinstance(payload, dict):
        return JSONResponse(status_code=200, content={"ok": True})

    dp = get_invite_dispatcher()

    async def _feed() -> None:
        try:
            update = Update.model_validate(payload, context={"bot": bot})
            await dp.feed_update(bot=bot, update=update)
        except Exception as e:
            logger.error(f"[InviteBot] error processing update: {e}")

    task = asyncio.create_task(_feed())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return JSONResponse(status_code=200, content={"ok": True})


async def drain_invite_tasks() -> None:
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)


__all__ = ["drain_invite_tasks", "reset_invite_api_state", "router"]
