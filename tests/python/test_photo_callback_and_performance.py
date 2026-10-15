"""Unit tests for photo callback navigation, black_list_service O(1) checks, and performance optimizations.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, User

from src.bot.handlers.callbacks_router import (
    handle_department_menu,
    handle_teacher_menu,
    safe_edit_or_replace_message,
    student_schedule_cache,
    teacher_table_cache,
    teacher_text_cache,
)
from src.cron.memory_trim import trim_process_memory
from src.services.black_list_service import black_list_service


def make_photo_msg(user_id=99200):
    u = User(id=user_id, is_bot=False, first_name="Тест", username="phototester")
    c = Chat(id=user_id, type="private")
    photo = [
        PhotoSize(file_id="photo_123", file_unique_id="uniq_123", width=100, height=100)
    ]
    msg = Message(
        message_id=99,
        date=datetime.now(timezone.utc),
        chat=c,
        from_user=u,
        photo=photo,
    )
    object.__setattr__(msg, "answer", AsyncMock())
    object.__setattr__(msg, "delete", AsyncMock())
    object.__setattr__(msg, "edit_text", AsyncMock())
    object.__setattr__(msg, "_bot", AsyncMock())
    return msg


def make_text_msg(user_id=99200, text="Hello"):
    u = User(id=user_id, is_bot=False, first_name="Тест", username="texttester")
    c = Chat(id=user_id, type="private")
    msg = Message(
        message_id=98,
        date=datetime.now(timezone.utc),
        chat=c,
        from_user=u,
        text=text,
    )
    object.__setattr__(msg, "answer", AsyncMock())
    object.__setattr__(msg, "delete", AsyncMock())
    object.__setattr__(msg, "edit_text", AsyncMock())
    object.__setattr__(msg, "_bot", AsyncMock())
    return msg


@pytest.mark.asyncio
async def test_safe_edit_or_replace_on_photo_message():
    """Verify that photo messages are deleted and new text message is answered."""
    msg = make_photo_msg()
    await safe_edit_or_replace_message(msg, "Новый текст", reply_markup=None)

    # edit_text must not be called on photo messages
    assert msg.edit_text.call_count == 0
    # delete must be called
    assert msg.delete.call_count == 1
    # answer must be called with the new text
    assert msg.answer.call_count == 1
    assert "Новый текст" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_safe_edit_or_replace_on_text_message():
    """Verify that text messages are smoothly edited in place."""
    msg = make_text_msg()
    await safe_edit_or_replace_message(msg, "Обновленный текст", reply_markup=None)

    assert msg.edit_text.call_count == 1
    assert "Обновленный текст" in msg.edit_text.call_args[0][0]
    assert msg.delete.call_count == 0
    assert msg.answer.call_count == 0


@pytest.mark.asyncio
async def test_safe_edit_or_replace_fallback_on_bad_request():
    """Verify that if edit_text fails with TelegramBadRequest, message is deleted and answered."""
    msg = make_text_msg()
    msg.edit_text.side_effect = TelegramBadRequest(
        method="editMessageText",
        message="Bad Request: there is no text in the message to edit",
    )

    await safe_edit_or_replace_message(msg, "Резервный текст", reply_markup=None)

    assert msg.delete.call_count == 1
    assert msg.answer.call_count == 1
    assert "Резервный текст" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_department_menu_from_photo_message(mock_db):
    """Verify that clicking back in teacher table photo message returns to department menu cleanly."""
    photo_msg = make_photo_msg()
    call = CallbackQuery(
        id="call_dept_back",
        from_user=photo_msg.from_user,
        chat_instance="inst_dept",
        message=photo_msg,
        data="department|0",
    )
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", photo_msg.bot)

    await handle_department_menu(call)

    assert call.answer.call_count == 1
    # Photo message deleted
    assert photo_msg.delete.call_count == 1
    # New department menu sent via answer
    assert photo_msg.answer.call_count == 1
    assert "Выбор кафедры" in photo_msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_teacher_menu_from_photo_message(mock_db):
    """Verify that clicking back from teacher table photo message returns to teacher catalog."""
    photo_msg = make_photo_msg()
    call = CallbackQuery(
        id="call_tch_back",
        from_user=photo_msg.from_user,
        chat_instance="inst_tch",
        message=photo_msg,
        data="teacher|101|0",
    )
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", photo_msg.bot)

    await handle_teacher_menu(call)

    assert call.answer.call_count == 1
    # Photo message deleted
    assert photo_msg.delete.call_count == 1
    # New teachers catalog sent via answer
    assert photo_msg.answer.call_count == 1
    assert "Выбор преподавателя" in photo_msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_black_list_service_crud_and_expiration(mock_db):
    """Verify BlackListService add_ban, add_mute, is_banned, is_muted, remove, and auto-expiration."""
    black_list_service.clear_cache()
    uid = 99301

    assert not black_list_service.is_banned(uid)
    assert not black_list_service.is_muted(uid)

    # 1. Add permanent ban
    await black_list_service.add_ban(uid, reason="Spamming", username="spammer")
    assert black_list_service.is_banned(uid)
    assert not black_list_service.is_muted(uid)

    rec = black_list_service.get_restriction(uid)
    assert rec is not None
    assert rec.type == "ban"
    assert rec.reason == "Spamming"

    # 2. Remove ban
    removed = await black_list_service.remove(uid)
    assert removed is True
    assert not black_list_service.is_banned(uid)

    # 3. Add temporary mute with future expiration
    future_time = datetime.now(timezone.utc) + timedelta(minutes=10)
    await black_list_service.add_mute(uid, until=future_time, reason="Flood")
    assert black_list_service.is_muted(uid)
    assert not black_list_service.is_banned(uid)

    # 4. Auto-expiration for past until
    past_time = datetime.now(timezone.utc) - timedelta(seconds=5)
    await black_list_service.add_mute(uid, until=past_time, reason="Old")
    # Immediate check should detect expiration and return False
    assert not black_list_service.is_muted(uid)


def test_schedule_ttl_caches_capacity():
    """Verify that schedule viewing caches are bounded TTLCaches preventing unbounded RAM leaks."""
    assert student_schedule_cache.maxsize == 150
    assert student_schedule_cache.ttl == 1800.0

    assert teacher_text_cache.maxsize == 150
    assert teacher_text_cache.ttl == 1800.0

    assert teacher_table_cache.maxsize == 100
    assert teacher_table_cache.ttl == 1200.0


def test_memory_trim_execution():
    """Verify that trim_process_memory runs cleanly."""
    # Should execute garbage collection without exceptions
    trim_process_memory()
