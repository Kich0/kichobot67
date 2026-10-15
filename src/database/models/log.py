"""Application database log document model."""

from typing import Any
from src.database.base import BaseMongoDoc


class Log(BaseMongoDoc):
    """General database log entry."""

    level: str | None = None
    message: str | None = None
    meta: Any | None = None

    class Settings:
        name = "logs"
