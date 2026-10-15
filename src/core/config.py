"""Configuration management for Kichobot using Pydantic Settings (v2).

Loads environment variables from system environment and .env file.
Provides strongly typed access and validation for all bot and service settings.
"""

from typing import Any, Literal
import hashlib
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === Server ===
    PORT: int = Field(default=5000, description="Web server port")
    HOST: str = Field(default="0.0.0.0", description="Web server host")

    @field_validator("PORT", "BOT_ID", mode="before", check_fields=False)
    @classmethod
    def _empty_int_to_default(cls, v: Any, info: Any) -> Any:
        if v == "" or v is None:
            return cls.model_fields[info.field_name].default
        return v

    # === Database ===
    DB_URI: str = Field(
        default="mongodb://localhost:27017/kichobot",
        description="MongoDB connection URI",
    )

    # === JWT Authentication ===
    JWT_ACCESS_SECRET: str | None = Field(default=None)
    JWT_REFRESH_SECRET: str | None = Field(default=None)

    # === University Portal (KSU) ===
    KSU_DOMAIN: str = Field(default="https://schedule.buketov.edu.kz")
    KSU_LOGIN: str | None = Field(default=None)
    KSU_PASSWORD: str | None = Field(default=None)

    # === Telegram Bot ===
    TG_TOKEN: str = Field(default="", description="Main Telegram Bot Token")
    LOG_CHANEL_ID: str = Field(default="", description="Telegram Channel ID for error logs")
    MEDIA_DUMP_CHANNEL_ID: str = Field(default="", description="Channel ID for media dumps")
    LOGGER_TG_TOKEN: str = Field(default="", description="Telegram Token for dedicated logger bot")
    BOT_ID: int = Field(default=0, description="Numeric Telegram ID of the bot")
    BOT_MODE: Literal["polling", "webhook"] = Field(
        default="polling", description="Mode: 'polling' or 'webhook'"
    )
    WEBHOOK_DOMAIN: str | None = Field(
        default=None, description="Public HTTPS domain for Webhook (e.g. on Render)"
    )
    WEBHOOK_PATH: str = Field(
        default="/bot/webhook", description="Path for incoming Telegram webhooks"
    )
    WEBHOOK_SECRET_TOKEN: str | None = Field(
        default=None, description="Secret token sent in X-Telegram-Bot-Api-Secret-Token"
    )

    # === Debug & Browser/Proxy (Legacy flags) ===
    DEBUG: bool = Field(default=False)
    START_BROWSER: bool = Field(default=False)
    AUTO_KSU_AUTH: bool = Field(default=False)
    USE_PROXY: bool = Field(default=False)
    HTTP_PROXY: str | None = Field(default=None)
    PROXY_LOGIN: str | None = Field(default=None)
    PROXY_PASSWORD: str | None = Field(default=None)
    USE_FREE_PROXIES: bool = Field(default=False)

    # === Official Buketov Schedule API ===
    SCHEDULE_API_URL: str = Field(
        default="https://schedule.karnu-buketov.edu.kz/api/v1/schedule",
        description="REST endpoint for schedule API",
    )
    SCHEDULE_API_KEY: str = Field(
        default="", description="API Bearer/Header Key for Buketov Schedule API"
    )

    # === External Backups & Logs ===
    GOOGLE_DRIVE_LOGS_DIR: str | None = Field(default=None)

    @property
    def webhook_url(self) -> str | None:
        """Computes the full public webhook URL."""
        if not self.WEBHOOK_DOMAIN:
            return None
        domain = self.WEBHOOK_DOMAIN.rstrip("/")
        path = self.WEBHOOK_PATH.lstrip("/")
        return f"{domain}/{path}"

    @property
    def resolved_webhook_secret(self) -> str | None:
        """Returns explicitly set WEBHOOK_SECRET_TOKEN or derives a deterministic 32-char token."""
        if self.WEBHOOK_SECRET_TOKEN:
            return self.WEBHOOK_SECRET_TOKEN.strip()
        if self.TG_TOKEN:
            return hashlib.sha256(f"{self.TG_TOKEN}_webhook_secret".encode()).hexdigest()[:32]
        return None

    def validate_critical_settings(self) -> list[str]:
        """Checks for missing critical production settings and returns a list of warnings."""
        missing = []
        critical_vars = {
            "DB_URI": self.DB_URI,
            "TG_TOKEN": self.TG_TOKEN,
            "LOG_CHANEL_ID": self.LOG_CHANEL_ID,
            "LOGGER_TG_TOKEN": self.LOGGER_TG_TOKEN,
            "BOT_ID": self.BOT_ID,
            "SCHEDULE_API_KEY": self.SCHEDULE_API_KEY,
        }
        for name, value in critical_vars.items():
            if not value:
                missing.append(name)
        if self.BOT_MODE == "webhook" and not self.WEBHOOK_DOMAIN:
            missing.append("WEBHOOK_DOMAIN (required when BOT_MODE=webhook)")
        return missing


settings = Settings()
