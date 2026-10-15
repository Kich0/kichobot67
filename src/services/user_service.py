"""User service for profile management and preferences."""

from __future__ import annotations

from typing import Any

from src.core.i18n import I18n
from src.core.logging import get_logger

logger = get_logger("user_service")


class UserService:
    """Manages user registration, language preferences, and profile data."""

    async def get_or_create_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> Any:
        """Retrieves an existing user document or creates a new one."""
        try:
            from src.database.models.user import User

            user = await User.find_one({"userId": user_id})
            if user:
                # Update username or names if changed
                changed = False
                clean_username = username.lstrip("@") if username else None
                if clean_username and user.username != clean_username:
                    user.username = clean_username
                    changed = True
                if first_name and user.first_name != first_name:
                    user.first_name = first_name
                    changed = True
                if last_name and user.last_name != last_name:
                    user.last_name = last_name
                    changed = True
                if changed:
                    await user.save()
                return user

            clean_username = username.lstrip("@") if username else None
            new_user = User(
                userId=user_id,
                username=clean_username,
                firstName=first_name,
                lastName=last_name,
                language="ru",
                isAdmin=False,
            )
            await new_user.insert()
            return new_user
        except Exception as e:
            logger.debug(f"User get_or_create skipped or errored: {e}")
            return None

    async def get_user_language(self, user_id: int) -> str:
        """Returns the user's preferred language ('ru' or 'kz'). Defaults to 'ru'."""
        try:
            from src.database.models.user import User

            user = await User.find_one({"userId": user_id})
            if user and user.language:
                return I18n.normalize_lang(user.language)
        except Exception:
            pass
        return "ru"

    async def set_user_language(self, user_id: int, language: str) -> None:
        """Updates user interface language."""
        norm_lang = I18n.normalize_lang(language)
        try:
            from src.database.models.user import User

            user = await User.find_one({"userId": user_id})
            if user:
                user.language = norm_lang
                await user.save()
            else:
                new_user = User(userId=user_id, language=norm_lang)
                await new_user.insert()
        except Exception as e:
            logger.debug(f"Failed to set user language: {e}")

    async def is_admin(self, user_id: int) -> bool:
        """Checks if a user has administrator rights."""
        from src.core.config import settings

        if settings.BOT_ID and user_id == settings.BOT_ID:
            return True

        try:
            from src.database.models.user import User

            user = await User.find_one({"userId": user_id})
            if user and user.is_admin:
                return True
        except Exception:
            pass
        return False

    async def find_user(self, query: str | int) -> Any:
        """Finds user by Telegram ID or @username."""
        try:
            from src.database.models.user import User

            if isinstance(query, int) or (isinstance(query, str) and query.isdigit()):
                return await User.find_one({"userId": int(query)})

            clean_q = str(query).lstrip("@").strip()
            return await User.find_one({"username": clean_q})
        except Exception:
            return None


user_service = UserService()

__all__ = ["UserService", "user_service"]
