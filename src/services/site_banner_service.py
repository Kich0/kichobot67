"""Service for dynamically managing the schedule disclaimer banner in Telegram messages.

Allows admins to switch between the original official site text and the
outdated schedule warning banner on the fly without server redeploys.
Persists state in MongoDB SystemSetting collection.
"""

from datetime import datetime, timezone
from src.core.i18n import i18n
from src.core.logging import get_logger

logger = get_logger("site_banner_service")

ORIGINAL_OFFICIAL_SITE_TEXTS: dict[str, str] = {
    "ru": '(официальный сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a>)',
    "kz": '(ресми сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a>)',
}

WARNING_OFFICIAL_SITE_TEXTS: dict[str, str] = {
    "ru": '🔴 Сейчас расписания могут быть неактуальными: официальный сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a> 🔴',
    "kz": '🔴 Қазір кестелер өзекті болмауы мүмкін: ресми сайт - <a href="https://schedule.karnu-buketov.edu.kz">schedule.karnu-buketov.edu.kz</a> 🔴',
}


class SiteBannerService:
    """Manages dynamic state of the official site comparison / warning banner."""

    SETTING_KEY = "schedule_warning_banner"

    def __init__(self) -> None:
        # Default active: True (warning banner is currently enabled by user request)
        self._is_warning_active: bool = True

    @property
    def is_warning_active(self) -> bool:
        """Returns True if the warning banner is currently active."""
        return self._is_warning_active

    def apply_to_i18n(self) -> None:
        """Applies the current banner text to global i18n translations dictionary."""
        texts = WARNING_OFFICIAL_SITE_TEXTS if self._is_warning_active else ORIGINAL_OFFICIAL_SITE_TEXTS
        for lang, val in texts.items():
            if lang in i18n._translations:
                i18n._translations[lang]["official_site_compare"] = val

    async def init_from_db(self) -> None:
        """Loads persistent banner configuration from MongoDB on application startup."""
        try:
            from src.database.models.system_setting import SystemSetting

            setting = await SystemSetting.find_one(SystemSetting.key == self.SETTING_KEY)
            if setting and setting.value is not None:
                self._is_warning_active = bool(setting.value)
                logger.info(
                    f"Loaded {self.SETTING_KEY}={self._is_warning_active} from MongoDB."
                )
        except Exception as e:
            logger.debug(f"Failed to load {self.SETTING_KEY} from DB during startup: {e}")

        # Synchronize loaded state with i18n
        self.apply_to_i18n()

    async def set_warning_active(self, active: bool) -> None:
        """Updates warning banner state in memory and persists to MongoDB."""
        self._is_warning_active = active
        self.apply_to_i18n()

        try:
            from src.database.models.system_setting import SystemSetting

            setting = await SystemSetting.find_one(SystemSetting.key == self.SETTING_KEY)
            now = datetime.now(timezone.utc)
            if not setting:
                setting = SystemSetting(
                    key=self.SETTING_KEY,
                    value=active,
                    updated_at=now,
                )
            else:
                setting.value = active
                setting.updated_at = now
            await setting.save()
            logger.info(f"Persisted {self.SETTING_KEY}={active} to MongoDB.")
        except Exception as e:
            logger.error(f"Failed to persist {self.SETTING_KEY} to MongoDB: {e}")


site_banner_service = SiteBannerService()

__all__ = [
    "SiteBannerService",
    "site_banner_service",
    "ORIGINAL_OFFICIAL_SITE_TEXTS",
    "WARNING_OFFICIAL_SITE_TEXTS",
]
