"""Service for dynamically managing the schedule disclaimer banner in Telegram messages.

Allows admins to switch between basic, alert (warning), and good banner statuses
either globally (all) or exclusively for admin preview testing (my).
Persists state in MongoDB SystemSetting collection.
"""

from datetime import datetime, timezone
from typing import Any
from src.core.i18n import i18n
from src.core.logging import get_logger

logger = get_logger("site_banner_service")

# Super-admin Telegram ID
SUPER_ADMIN_ID = 1516988645

BANNER_TEXTS: dict[str, dict[str, str]] = {
    "basic": {
        "ru": '(официальный сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a>)',
        "kz": '(ресми сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a>)',
    },
    "alert": {
        "ru": '🔴 Сейчас расписания могут быть неактуальными: официальный сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a> 🔴',
        "kz": '🔴 Қазір кестелер өзекті болмауы мүмкін: ресми сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a> 🔴',
    },
    "good": {
        "ru": '🟢 Все расписания сейчас актуальные, всё работает хорошо, бот работает штатно. Официальный сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a> 🟢',
        "kz": '🟢 Барлық кестелер қазір өзекті, бәрі жақсы жұмыс істеп тұр, бот штаттық режимде жұмыс істейді. Ресми сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a> 🟢',
    },
}

# Backward compatibility references
ORIGINAL_OFFICIAL_SITE_TEXTS = BANNER_TEXTS["basic"]
WARNING_OFFICIAL_SITE_TEXTS = BANNER_TEXTS["alert"]
GOOD_OFFICIAL_SITE_TEXTS = BANNER_TEXTS["good"]


class SiteBannerService:
    """Manages dynamic state of the official site comparison / status banner."""

    GLOBAL_SETTING_KEY = "schedule_banner_mode_global"
    ADMIN_SETTING_KEY = "schedule_banner_mode_admin"
    LEGACY_SETTING_KEY = "schedule_warning_banner"

    def __init__(self) -> None:
        self._global_mode: str = "basic"
        self._admin_mode: str | None = None

    def reset(self) -> None:
        """Resets banner state to defaults (used for testing isolation)."""
        self._global_mode = "basic"
        self._admin_mode = None
        self.apply_to_i18n()

    @property
    def global_mode(self) -> str:
        """Returns the active global banner mode (basic, alert, good)."""
        return self._global_mode

    @property
    def admin_mode(self) -> str | None:
        """Returns the admin preview banner mode, or None if matching global."""
        return self._admin_mode

    @property
    def is_warning_active(self) -> bool:
        """Legacy helper: True if global mode is alert."""
        return self._global_mode == "alert"

    def _is_admin_viewer(self, viewer_user_id: int | str | None) -> bool:
        """Checks if viewer is super-admin."""
        if viewer_user_id is None:
            return False
        try:
            return int(viewer_user_id) == SUPER_ADMIN_ID
        except (ValueError, TypeError):
            return False

    def get_effective_mode(self, viewer_user_id: int | str | None = None) -> str:
        """Resolves active banner mode for a specific viewer (checks admin override first)."""
        if self._admin_mode and self._is_admin_viewer(viewer_user_id):
            return self._admin_mode
        return self._global_mode

    def get_banner_text(self, lang: str, viewer_user_id: int | str | None = None) -> str:
        """Retrieves formatted banner HTML for the resolved mode and language."""
        mode = self.get_effective_mode(viewer_user_id)
        mode_dict = BANNER_TEXTS.get(mode, BANNER_TEXTS["basic"])
        lang_key = "kz" if lang in ("kz", "каз") else "ru"
        return mode_dict.get(lang_key, mode_dict["ru"])

    def apply_to_i18n(self) -> None:
        """Applies global banner text to global i18n translations dictionary."""
        texts = BANNER_TEXTS.get(self._global_mode, BANNER_TEXTS["basic"])
        for lang, val in texts.items():
            if lang in i18n._translations:
                i18n._translations[lang]["official_site_compare"] = val

    async def init_from_db(self) -> None:
        """Loads persistent banner configuration from MongoDB on application startup."""
        try:
            from src.database.models.system_setting import SystemSetting

            # 1. Global setting
            g_setting = await SystemSetting.find_one(SystemSetting.key == self.GLOBAL_SETTING_KEY)
            if g_setting and g_setting.value:
                val = str(g_setting.value).lower().strip()
                if val in BANNER_TEXTS:
                    self._global_mode = val
            else:
                # Check legacy boolean setting if exists
                legacy = await SystemSetting.find_one(SystemSetting.key == self.LEGACY_SETTING_KEY)
                if legacy and legacy.value is not None:
                    self._global_mode = "alert" if bool(legacy.value) else "basic"

            # 2. Admin preview setting
            a_setting = await SystemSetting.find_one(SystemSetting.key == self.ADMIN_SETTING_KEY)
            if a_setting and a_setting.value:
                val = str(a_setting.value).lower().strip()
                if val in BANNER_TEXTS:
                    self._admin_mode = val
                elif val in ("none", "null", "reset", "off"):
                    self._admin_mode = None

            logger.info(
                f"Loaded banner config: global={self._global_mode}, admin={self._admin_mode}"
            )
        except Exception as e:
            logger.debug(f"Failed to load banner settings from DB during startup: {e}")

        # Synchronize loaded state with i18n
        self.apply_to_i18n()

    async def set_mode(self, mode: str, scope: str = "all") -> None:
        """Updates banner mode for specified scope ('all' or 'my') and persists to MongoDB."""
        clean_mode = mode.lower().strip()
        if clean_mode not in BANNER_TEXTS and clean_mode not in ("reset", "none", "off"):
            raise ValueError(f"Unknown banner mode: {mode}")

        clean_scope = scope.lower().strip()
        is_my = clean_scope in ("my", "admin", "me")

        if is_my:
            self._admin_mode = None if clean_mode in ("reset", "none", "off") else clean_mode
            setting_key = self.ADMIN_SETTING_KEY
            persisted_val = self._admin_mode
        else:
            if clean_mode in ("reset", "none", "off"):
                clean_mode = "basic"
            self._global_mode = clean_mode
            setting_key = self.GLOBAL_SETTING_KEY
            persisted_val = self._global_mode
            self.apply_to_i18n()

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

    async def set_warning_active(self, active: bool) -> None:
        """Backward-compatible setter for legacy callers."""
        await self.set_mode("alert" if active else "basic", scope="all")


site_banner_service = SiteBannerService()

__all__ = [
    "SiteBannerService",
    "site_banner_service",
    "BANNER_TEXTS",
    "ORIGINAL_OFFICIAL_SITE_TEXTS",
    "WARNING_OFFICIAL_SITE_TEXTS",
    "GOOD_OFFICIAL_SITE_TEXTS",
    "SUPER_ADMIN_ID",
]
