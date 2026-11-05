"""Service for dynamically managing schedule UI themes (basic, halloween, happy new year).

Allows admins to switch between themes either globally (all) or exclusively
for admin preview testing (my).
Persists state in MongoDB SystemSetting collection.
"""

from datetime import datetime, timezone
from src.core.logging import get_logger

logger = get_logger("schedule_theme_service")

SUPER_ADMIN_ID = 1516988645

SUPPORTED_THEMES = {
    "basic",
    "halloween",
    "newyear",
}

THEME_ALIASES: dict[str, str] = {
    "basic": "basic",
    "default": "basic",
    "standard": "basic",
    "orig": "basic",
    "original": "basic",
    "базовый": "basic",
    "стандарт": "basic",
    "halloween": "halloween",
    "хэллоуин": "halloween",
    "хеллоуин": "halloween",
    "хэлуин": "halloween",
    "newyear": "newyear",
    "new_year": "newyear",
    "happy_new_year": "newyear",
    "happynewyear": "newyear",
    "happy new year": "newyear",
    "новый год": "newyear",
    "новыйгод": "newyear",
    "новогодний": "newyear",
}


class ScheduleThemeService:
    """Manages active visual theme for student and teacher schedules."""

    GLOBAL_SETTING_KEY = "schedule_theme_global"
    ADMIN_SETTING_KEY = "schedule_theme_admin"

    def __init__(self) -> None:
        self._global_theme: str = "basic"
        self._admin_theme: str | None = None
        self._has_explicit_global: bool = False

    @property
    def global_theme(self) -> str:
        """Returns the active global theme ('basic', 'halloween', 'newyear')."""
        return self._global_theme

    @property
    def admin_theme(self) -> str | None:
        """Returns the admin preview theme, or None if matching global."""
        return self._admin_theme

    def reset(self) -> None:
        """Resets theme state to defaults (used for testing isolation)."""
        self._global_theme = "basic"
        self._admin_theme = None
        self._has_explicit_global = False

    def _is_admin_viewer(self, viewer_user_id: int | str | None) -> bool:
        """Checks if viewer is super-admin."""
        if viewer_user_id is None:
            return False
        try:
            return int(viewer_user_id) == SUPER_ADMIN_ID
        except (ValueError, TypeError):
            return False

    def normalize_theme_name(self, raw: str) -> str | None:
        """Normalizes user-supplied theme string to canonical name."""
        clean = (raw or "").lower().strip()
        return THEME_ALIASES.get(clean)

    def get_active_theme(self, viewer_user_id: int | str | None = None) -> str:
        """Resolves active theme for a specific viewer (checks admin preview first)."""
        if self._admin_theme and self._is_admin_viewer(viewer_user_id):
            return self._admin_theme
        return self._global_theme

    async def init_from_db(self) -> None:
        """Loads persistent theme configuration from MongoDB on application startup."""
        try:
            from src.database.models.system_setting import SystemSetting

            # 1. Global setting
            g_setting = await SystemSetting.find_one(SystemSetting.key == self.GLOBAL_SETTING_KEY)
            if g_setting and g_setting.value:
                norm = self.normalize_theme_name(str(g_setting.value))
                if norm:
                    self._global_theme = norm
                    self._has_explicit_global = True

            # 2. Admin preview setting
            a_setting = await SystemSetting.find_one(SystemSetting.key == self.ADMIN_SETTING_KEY)
            if a_setting and a_setting.value:
                val = str(a_setting.value).lower().strip()
                norm = self.normalize_theme_name(val)
                if norm:
                    self._admin_theme = norm
                elif val in ("none", "null", "reset", "off"):
                    self._admin_theme = None

            logger.info(
                f"Loaded schedule theme config: global={self._global_theme}, admin={self._admin_theme}"
            )
        except Exception as e:
            logger.debug(f"Failed to load schedule theme settings from DB during startup: {e}")

    async def set_theme(self, theme: str, scope: str = "all") -> str:
        """Updates theme for specified scope ('all' or 'my') and persists to MongoDB.

        Returns canonical theme name applied.
        """
        clean_theme = theme.lower().strip()
        is_reset = clean_theme in ("reset", "none", "off", "cancel")
        norm = self.normalize_theme_name(clean_theme)

        if not norm and not is_reset:
            raise ValueError(f"Unknown theme: {theme}")

        clean_scope = scope.lower().strip()
        is_my = clean_scope in ("my", "admin", "me")

        if is_my:
            self._admin_theme = None if is_reset else norm
            setting_key = self.ADMIN_SETTING_KEY
            persisted_val = self._admin_theme
            result = self._admin_theme or self._global_theme
        else:
            if is_reset or not norm:
                norm = "basic"
            self._global_theme = norm
            self._has_explicit_global = True
            setting_key = self.GLOBAL_SETTING_KEY
            persisted_val = self._global_theme
            result = self._global_theme

        try:
            from src.database.models.system_setting import SystemSetting

            now = datetime.now(timezone.utc)
            setting = await SystemSetting.find_one(SystemSetting.key == setting_key)
            if not setting:
                setting = SystemSetting(
                    key=setting_key,
                    value=persisted_val,
                    updated_at=now,
                )
            else:
                setting.value = persisted_val
                setting.updated_at = now
            await setting.save()
            logger.info(f"Persisted {setting_key}={persisted_val} to MongoDB.")
        except Exception as e:
            logger.error(f"Failed to persist {setting_key} to MongoDB: {e}")

        return result


schedule_theme_service = ScheduleThemeService()

__all__ = [
    "ScheduleThemeService",
    "schedule_theme_service",
    "SUPPORTED_THEMES",
    "THEME_ALIASES",
]
