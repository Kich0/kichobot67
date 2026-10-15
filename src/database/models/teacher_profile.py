"""Teacher profile document model."""

from pydantic import Field
from src.database.base import BaseMongoDoc


class TeacherProfile(BaseMongoDoc):
    """Scraped / cached university profile for a teacher."""

    name: str | None = Field(default=None)
    faculty: str | None = Field(default=None)
    department: str | None = Field(default=None)
    href: str | None = Field(default=None)

    class Settings:
        name = "teacher-profiles"
