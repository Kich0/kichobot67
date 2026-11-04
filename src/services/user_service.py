"""User service for profile management and preferences."""

from __future__ import annotations

import asyncio
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
        self._profile_cache: TTLCache[int, tuple[str | None, str | None, str | None]] = TTLCache(maxsize=10000, ttl=3600.0)

    def clear_cache(self) -> None:
        """Clears the in-memory language and profile caches."""
        self._lang_cache.clear()
        self._profile_cache.clear()

    def touch_user_profile(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        """Lightweight non-blocking in-memory check to sync changed profile data into MongoDB in background."""
        if not user_id:
            return
        clean_username = username.lstrip("@") if username else None
        current_profile = (clean_username, first_name, last_name)
        cached = self._profile_cache.get(user_id)
        if cached == current_profile:
            return
        self._profile_cache[user_id] = current_profile

        try:
            asyncio.create_task(self._sync_profile_to_db(user_id, clean_username, first_name, last_name))
        except Exception as e:
            logger.debug(f"Could not launch profile sync task for {user_id}: {e}")

    async def _sync_profile_to_db(
        self,
        user_id: int,
        clean_username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        """Synchronizes updated user profile attributes into MongoDB."""
        try:
            from datetime import datetime, timezone
            from src.database.models.user import User

            res = await User.find({"userId": user_id}).update({"$set": {
                "username": clean_username,
                "firstName": first_name,
                "lastName": last_name,
                "updatedAt": datetime.now(timezone.utc),
            }})
            # If user document does not exist in DB yet, create it
            if getattr(res, "matched_count", 0) == 0:
                await self.get_or_create_user(
                    user_id=user_id,
                    username=clean_username,
                    first_name=first_name,
                    last_name=last_name,
                )
        except Exception as e:
            logger.debug(f"Failed to sync user profile in background for {user_id}: {e}")

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

            clean_username = username.lstrip("@") if username else None
            client_id = id(get_client())
            user = await User.find_one({"userId": user_id})
            if user:
                # Update username or names if changed
                changed = False
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
                self._profile_cache[user_id] = (clean_username, first_name, last_name)
                if user.language:
                    self._lang_cache[(client_id, user_id)] = I18n.normalize_lang(user.language)
                return user

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
                self._profile_cache[user_id] = (clean_username, first_name, last_name)
                self._lang_cache[(client_id, user_id)] = "ru"
                return new_user
            except DuplicateKeyError:
                existing = await User.find_one({"userId": user_id})
                if existing:
                    changed = False
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
                    self._profile_cache[user_id] = (clean_username, first_name, last_name)
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

    async def is_admin(self, user_id: int | str | None) -> bool:
        """Checks if a user has administrator rights."""
        if not user_id:
            return False
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return False

        # Owner / Developer has unconditional administrator privileges
        if uid == 1516988645:
            return True

        from src.core.config import settings

        if settings.BOT_ID and uid == settings.BOT_ID:
            return True

        try:
            from src.database.models.user import User

            user = await User.find_one({"userId": uid})
            if user and user.is_admin:
                return True
        except Exception:
            pass
        return False

    async def find_user(self, query: str | int | None) -> Any:
        """Finds a single user by Telegram ID, @username (current or historical), or name."""
        if query is None:
            return None
        users = await self.search_users(query, limit=1)
        return users[0] if users else None

    async def search_users(self, query: str | int | None, limit: int = 10) -> list[Any]:
        """Searches users by Telegram ID, @username (current or historical), or firstName/lastName."""
        if query is None:
            return []
        try:
            import re
            from beanie.operators import RegEx
            from src.database.models.user import User
            from src.database.models.user_action import UserAction

            if isinstance(query, int):
                u = await User.find_one({"userId": int(query)})
                return [u] if u else []

            query_str = str(query).strip()
            if not query_str:
                return []

            # 1. Numeric Telegram ID (supports both positive user IDs and negative supergroup IDs)
            if query_str.isdigit() or (query_str.startswith("-") and query_str[1:].isdigit()):
                u = await User.find_one({"userId": int(query_str)})
                if u:
                    return [u]

            clean_q = query_str.lstrip("@").strip()
            if not clean_q:
                return []

            escaped = re.escape(clean_q)

            # 2. Exact match on username (both bare and with leading @)
            user = await User.find_one({"username": clean_q}) or await User.find_one({"username": f"@{clean_q}"})
            if user:
                return [user]

            # 3. Case-insensitive exact match on username
            user = await User.find_one(RegEx("username", f"^@?{escaped}$", "i"))
            if user:
                return [user]

            # 4. Search in UserAction logs for historical username (e.g. user changed username)
            if query_str.startswith("@") or " " not in clean_q:
                hist_action = await UserAction.find_one(RegEx("username", f"^@?{escaped}$", "i"))
                if hist_action and hist_action.user_id:
                    u = await User.find_one({"userId": hist_action.user_id})
                    if u:
                        return [u]

            # 5. Search by First Name or Last Name
            words = clean_q.split()
            if len(words) == 1:
                w_escaped = re.escape(words[0])
                name_filter = {
                    "$or": [
                        {"firstName": {"$regex": w_escaped, "$options": "i"}},
                        {"lastName": {"$regex": w_escaped, "$options": "i"}},
                        {"username": {"$regex": w_escaped, "$options": "i"}},
                    ]
                }
            else:
                w1 = re.escape(words[0])
                w2 = re.escape(" ".join(words[1:]))
                name_filter = {
                    "$or": [
                        {"firstName": {"$regex": escaped, "$options": "i"}},
                        {"lastName": {"$regex": escaped, "$options": "i"}},
                        {
                            "$and": [
                                {"firstName": {"$regex": w1, "$options": "i"}},
                                {"lastName": {"$regex": w2, "$options": "i"}},
                            ]
                        },
                    ]
                }

            matched_users = await User.find(name_filter).limit(limit).to_list()
            return matched_users
        except Exception as e:
            logger.debug(f"Failed to search users for '{query}': {e}")
            return []


user_service = UserService()

__all__ = ["UserService", "user_service"]
