"""Catalog discovery document model for tracking brand-new university entities."""

from datetime import datetime, timezone
from typing import Any, ClassVar
from pymongo import IndexModel, ASCENDING, DESCENDING
from pydantic import Field, field_validator

from src.database.base import BaseMongoDoc, ItemIdDescriptor


class CatalogDiscovery(BaseMongoDoc):
    """Historical record of brand-new university groups or teachers discovered during official catalog scans.

    Stored permanently (without TTL) in MongoDB collection 'catalog_discoveries'
    so administrators can verify the exact arrival date, entity type, IDs, and metadata
    for every new group or teacher ever added to Buketov KarU.
    """

    entity_type: str = Field(description="'group' or 'teacher'")
    entity_id: int = Field(alias="id")
    name: str
    faculty_id: int | None = Field(default=None, alias="facultyId")
    faculty_name: str | None = Field(default=None, alias="facultyName")
    department_id: int | None = Field(default=None, alias="departmentId")
    department_name: str | None = Field(default=None, alias="departmentName")
    course: int | None = Field(default=None)
    language: str | None = Field(default=None)
    discovered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="discoveredAt",
    )
    scan_source: str = Field(default="weekly_catalog_sync", alias="scanSource")
    meta: dict[str, Any] = Field(default_factory=dict)

    item_id: ClassVar[Any] = ItemIdDescriptor("entity_id")

    @field_validator("faculty_id", "department_id", "course", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        if v == "" or v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    @field_validator("discovered_at", mode="before")
    @classmethod
    def _coerce_discovered_at(cls, v: Any) -> Any:
        return v if v is not None else datetime.now(timezone.utc)

    class Settings:
        name = "catalog_discoveries"
        indexes = [
            IndexModel([("entity_type", ASCENDING), ("id", ASCENDING)], unique=True),
            IndexModel([("discoveredAt", DESCENDING)]),
            IndexModel([("name", ASCENDING)]),
        ]
