"""UI/UX parity verification tests matching Node.js commit 153 exactly.

Tests:
1. Exact reply keyboard buttons for Russian and Kazakh.
2. Exact message texts for /start, /new, /help, /remove.
3. Inline keyboard layouts for language selection and new schedule.
4. Message deletion and welcome page redirection on language switch.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from aiogram.types import CallbackQuery, Chat, Message, ReplyKeyboardRemove, User

from src.bot.handlers.callbacks_router import handle_set_language
from src.bot.handlers.commands_router import (
    handle_help,
    handle_new_schedule,
    handle_remove_keyboard,
    handle_start,
)
from src.bot.keyboards.inline import get_language_keyboard, get_new_schedule_keyboard
from src.bot.keyboards.reply import get_main_reply_keyboard
from src.core.i18n import t


def make_msg(user_id=88100, text="/start", username="ui_tester"):
    u = User(id=user_id, is_bot=False, first_name="Тестер", username=username)
    c = Chat(id=user_id, type="private")
    msg = Message(
        message_id=55,
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


def make_call(user_id=88100, data="languageIsRu"):
    msg = make_msg(user_id=user_id)
    call = CallbackQuery(
        id="call_ui",
        from_user=msg.from_user,
        chat_instance="ui_inst",
        message=msg,
        data=data,
    )
    object.__setattr__(call, "answer", AsyncMock())
    object.__setattr__(call, "_bot", msg.bot)
    return call


def test_main_reply_keyboard_ru_and_kz():
    # Russian reply keyboard
    kb_ru = get_main_reply_keyboard(lang="ru")
    assert kb_ru.resize_keyboard is True
    assert len(kb_ru.keyboard) == 2
    # Row 1
    assert kb_ru.keyboard[0][0].text == "🗒 Новое расписание"
    assert kb_ru.keyboard[0][1].text == "❓ Помощь "
    # Row 2
    assert kb_ru.keyboard[1][0].text == "🗓 Расписание преподавателя"
    assert kb_ru.keyboard[1][1].text == "🗓 Расписание студента"

    # Kazakh reply keyboard
    kb_kz = get_main_reply_keyboard(lang="kz")
    assert kb_kz.resize_keyboard is True
    assert len(kb_kz.keyboard) == 2
    # Row 1
    assert kb_kz.keyboard[0][0].text == "🗒 Жаңа кесте"
    assert kb_kz.keyboard[0][1].text == "❓ Көмек"
    # Row 2
    assert kb_kz.keyboard[1][0].text == "🗓 Оқытушының кестесі"
    assert kb_kz.keyboard[1][1].text == "🗓 Студенттің кестесі"


@pytest.mark.asyncio
async def test_start_command_parity(mock_db):
    msg = make_msg(text="/start")
    await handle_start(msg)

    assert msg.answer.call_count == 1
    text, kwargs = msg.answer.call_args[0][0], msg.answer.call_args[1]

    # Exact bilingual prompt matching Node.js startCommandController.js
    assert text == "🌍 Выберите язык интерфейса. \n🌍 Интерфейстің тіл тандаңыз. "

    kb = kwargs["reply_markup"]
    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[0][0].text == "Русский"
    assert kb.inline_keyboard[0][0].callback_data == "languageIsRu"
    assert kb.inline_keyboard[1][0].text == "Қазақ"
    assert kb.inline_keyboard[1][0].callback_data == "languageIsKz"
    assert kb.inline_keyboard[2][1].text == "❌"
    assert kb.inline_keyboard[2][1].callback_data == "delete"


@pytest.mark.asyncio
async def test_new_schedule_command_parity(mock_db):
    msg = make_msg(text="/new")
    await handle_new_schedule(msg)

    assert msg.answer.call_count == 1
    text, kwargs = msg.answer.call_args[0][0], msg.answer.call_args[1]

    # 3-line format matching Node.js newScheduleCommandController.js
    assert text.startswith("👋 🛰 Чье расписание тебе нужно?")
    assert "<i>💡 P.S. Если тебе лень тыкать по кнопочкам - можешь воспользоваться <b>Поиском</b>.</i>" in text
    assert "📖 Подробнее /search" in text

    kb = kwargs["reply_markup"]
    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[0][0].text == f"🤓 {t('student', 'ru')}"
    assert kb.inline_keyboard[0][0].callback_data == "faculty|0"
    assert kb.inline_keyboard[1][0].text == f"👩‍🏫 {t('teacher', 'ru')}"
    assert kb.inline_keyboard[1][0].callback_data == "department|0"
    assert kb.inline_keyboard[2][1].text == "❌"
    assert kb.inline_keyboard[2][1].callback_data == "delete"


@pytest.mark.asyncio
async def test_language_selection_redirect_to_welcome_page(mock_db):
    call = make_call(data="languageIsRu")
    await handle_set_language(call)

    # 1. Previous message deleted
    assert call.message.delete.call_count == 1
    # 2. Welcome page answered
    assert call.message.answer.call_count == 1
    ans_text, ans_kwargs = call.message.answer.call_args[0][0], call.message.answer.call_args[1]
    assert ans_text == t("welcome_page", "ru")
    # 3. Main reply keyboard attached
    reply_kb = ans_kwargs["reply_markup"]
    assert reply_kb.keyboard[0][0].text == "🗒 Новое расписание"


@pytest.mark.asyncio
async def test_help_and_remove_commands(mock_db):
    # /help
    msg_help = make_msg(text="/help")
    await handle_help(msg_help)
    assert msg_help.answer.call_count == 1
    assert msg_help.answer.call_args[0][0] == t("help_command_content", "ru")

    # /remove
    msg_rm = make_msg(text="/remove")
    await handle_remove_keyboard(msg_rm)
    assert msg_rm.answer.call_count == 1
    assert msg_rm.answer.call_args[0][0] == t("keyboard_deleted", "ru")
    assert isinstance(msg_rm.answer.call_args[1]["reply_markup"], ReplyKeyboardRemove)


@pytest.mark.asyncio
async def test_settings_and_language_select_prompt_parity(mock_db):
    from src.bot.handlers.callbacks_router import handle_language_select_menu
    from src.bot.handlers.commands_router import handle_settings

    # /settings command
    msg_sett = make_msg(text="/settings")
    await handle_settings(msg_sett)
    assert msg_sett.answer.call_count == 1
    assert msg_sett.answer.call_args[0][0] == "🌍 Выберите язык интерфейса. \n🌍 Интерфейстің тіл тандаңыз. "

    # callback 'language_select'
    call_lang = make_call(data="language_select")
    await handle_language_select_menu(call_lang)
    assert call_lang.message.edit_text.call_count == 1
    assert call_lang.message.edit_text.call_args[0][0] == "🌍 Выберите язык интерфейса. \n🌍 Интерфейстің тіл тандаңыз. "
