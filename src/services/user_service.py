"""User service for profile management and preferences."""

from __future__ import annotations

from typing import Any

from cachetools import TTLCache

from src.core.i18n import I18n
from src.core.logging import get_logger
from src.database.connection import get_client

logger = get_logger("user_service")


class UserService:
    """Manages user registration, language preferences, and profile data."""

    def __init__(self) -> None:
        self._lang_cache: TTLCache[tuple[int, int], str] = TTLCache(maxsize=5000, ttl=1800.0)

    def clear_cache(self) -> None:
        """Clears the in-memory language cache."""
        self._lang_cache.clear()

    async def get_or_create_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> Any:
        """Retrieves an existing user document or creates a new one."""
        try:
            from pymongo.errors import DuplicateKeyError
            from src.database.models.user import User

            client_id = id(get_client())
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
                if user.language:
                    self._lang_cache[(client_id, user_id)] = I18n.normalize_lang(user.language)
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
            try:
                await new_user.insert()
                self._lang_cache[(client_id, user_id)] = "ru"
                return new_user
            except DuplicateKeyError:
                existing = await User.find_one({"userId": user_id})
                if existing:
                    changed = False
                    clean_username = username.lstrip("@") if username else None
                    if clean_username and existing.username != clean_username:
                        existing.username = clean_username
                        changed = True
                    if first_name and existing.first_name != first_name:
                        existing.first_name = first_name
                        changed = True
                    if last_name and existing.last_name != last_name:
                        existing.last_name = last_name
                        changed = True
                    if changed:
                        await existing.save()
                    if existing.language:
                        self._lang_cache[(client_id, user_id)] = I18n.normalize_lang(existing.language)
                    return existing
                return new_user
        except Exception as e:
            logger.debug(f"User get_or_create skipped or errored: {e}")
            return None

    async def get_user_language(self, user_id: int) -> str:
        """Returns the user's preferred language ('ru' or 'kz'). Defaults to 'ru'."""
        if not user_id:
            return "ru"
        client_id = id(get_client())
        cached = self._lang_cache.get((client_id, user_id))
        if cached is not None:
            return cached
        try:
            from src.database.models.user import User

            user = await User.find_one({"userId": user_id})
            if user and user.language:
                norm_lang = I18n.normalize_lang(user.language)
                self._lang_cache[(client_id, user_id)] = norm_lang
                return norm_lang
        except Exception:
            pass
        return "ru"

    async def set_user_language(self, user_id: int, language: str) -> None:
        """Updates user interface language."""
        norm_lang = I18n.normalize_lang(language)
        client_id = id(get_client())
        self._lang_cache[(client_id, user_id)] = norm_lang
        try:
            from pymongo.errors import DuplicateKeyError
            from src.database.models.user import User

            user = await User.find_one({"userId": user_id})
            if user:
                user.language = norm_lang
                await user.save()
            else:
                new_user = User(userId=user_id, language=norm_lang)
                try:
                    await new_user.insert()
                except DuplicateKeyError:
                    existing = await User.find_one({"userId": user_id})
                    if existing:
                        existing.language = norm_lang
                        await existing.save()
        except Exception as e:
            logger.debug(f"Failed to set user language: {e}")

    async def set_user_schedule_preference(
        self,
        user_id: int,
        schedule_type: str,
        group: str | int | None = None,
        teacher: str | int | None = None,
    ) -> None:
        """Saves the user's last viewed student group or teacher preference in background."""
        if not user_id:
            return
        try:
            from src.database.models.user import User

            user = await User.find_one({"userId": user_id})
            if not user:
                return
            changed = False
            if user.schedule_type != schedule_type:
                user.schedule_type = schedule_type
                changed = True
            if group is not None:
                norm_group: int | None = None
                if isinstance(group, int):
                    norm_group = group
                elif isinstance(group, str) and group.strip().isdigit():
                    norm_group = int(group.strip())
                if norm_group is not None and norm_group > 0 and user.group != norm_group:
                    user.group = norm_group
                    changed = True
            if teacher is not None:
                norm_teacher: int | str | None = None
                if isinstance(teacher, int):
                    norm_teacher = teacher if teacher > 0 else None
                elif isinstance(teacher, str):
                    s = teacher.strip()
                    if s:
                        norm_teacher = int(s) if s.isdigit() else s
                if norm_teacher is not None and user.teacher != norm_teacher:
                    user.teacher = norm_teacher
                    changed = True
            if changed:
                await user.save()
        except Exception as e:
            logger.debug(f"Failed to save user schedule preference: {e}")

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
