"""System settings document model for dynamic runtime configurations."""

from datetime import datetime, timezone
from typing import Any
from beanie import Document
from pydantic import Field


class SystemSetting(Document):
    """Key-value dynamic configuration document."""

    key: str = Field(unique=True, index=True)
    value: Any = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "system_settings"
