"""Unit tests for configuration loading and validation."""

import pytest
from src.core.config import Settings


def test_default_settings():
    """Tests default values for Settings."""
    cfg = Settings(
        _env_file=None,
        TG_TOKEN="123456:ABC-DEF",
        LOG_CHANEL_ID="-100123456",
        LOGGER_TG_TOKEN="123456:LOGGER",
        BOT_ID=987654321,
        SCHEDULE_API_KEY="test_key",
        DB_URI="mongodb://localhost:27017/test_bot",
    )
    assert cfg.PORT == 5000
    assert cfg.HOST == "0.0.0.0"
    assert cfg.BOT_MODE == "polling"
    assert cfg.DEBUG is False
    assert cfg.BOT_ID == 987654321
    assert cfg.webhook_url is None
    assert cfg.validate_critical_settings() == []



def test_webhook_url_and_secret():
    """Tests webhook URL generation and secret derivation."""
    cfg = Settings(
        TG_TOKEN="test_token_123",
        WEBHOOK_DOMAIN="https://bot.example.com",
        WEBHOOK_PATH="/bot/webhook",
    )
    assert cfg.webhook_url == "https://bot.example.com/bot/webhook"
    secret = cfg.resolved_webhook_secret
    assert secret is not None
    assert len(secret) == 32

    # When explicit secret is provided
    cfg2 = Settings(
        TG_TOKEN="test_token_123",
        WEBHOOK_SECRET_TOKEN="my_custom_secret",
    )
    assert cfg2.resolved_webhook_secret == "my_custom_secret"


def test_critical_settings_validation():
    """Tests warning list for missing critical production parameters."""
    cfg = Settings(
        TG_TOKEN="",
        LOG_CHANEL_ID="",
        LOGGER_TG_TOKEN="",
        BOT_ID=0,
        SCHEDULE_API_KEY="",
        DB_URI="",
    )
    missing = cfg.validate_critical_settings()
    assert "DB_URI" in missing
    assert "TG_TOKEN" in missing
    assert "LOG_CHANEL_ID" in missing
    assert "LOGGER_TG_TOKEN" in missing
    assert "BOT_ID" in missing
    assert "SCHEDULE_API_KEY" in missing


def test_webhook_mode_requires_domain():
    """Tests that webhook mode without domain reports missing configuration."""
    cfg = Settings(
        TG_TOKEN="token",
        LOG_CHANEL_ID="chan",
        LOGGER_TG_TOKEN="l_token",
        BOT_ID=123,
        SCHEDULE_API_KEY="key",
        DB_URI="mongodb://localhost:27017/db",
        BOT_MODE="webhook",
        WEBHOOK_DOMAIN=None,
    )
    missing = cfg.validate_critical_settings()
    assert any("WEBHOOK_DOMAIN" in m for m in missing)


def test_empty_string_integer_settings():
    """Verifies that empty string environment variables for PORT and BOT_ID fall back to defaults."""
    cfg = Settings(PORT="", BOT_ID="")
    assert cfg.PORT == 5000
    assert cfg.BOT_ID == 0
