"""Base document for all MongoDB collections in Kichobot.

Inherits from Beanie's Document and configures Pydantic v2 ConfigDict:
- extra="ignore" to silently drop legacy Mongoose internal fields (such as __v).
- populate_by_name=True to allow populating models using either Python attribute names
  or MongoDB schema aliases (e.g. userId <-> user_id, createdAt <-> created_at).
"""

from datetime import datetime, timezone
from typing import Any, ClassVar
from bson import ObjectId
from beanie import Document
from pydantic import ConfigDict, Field, model_validator


class ItemIdDescriptor:
    """Descriptor that bridges domain ID field across both Beanie queries and model instances."""

    def __init__(self, target_attr: str) -> None:
        self.target_attr = target_attr

    def __get__(self, instance: Any, owner: Any) -> Any:
        if instance is None:
            # Called on class (e.g. Group.item_id == 55) -> delegates to Beanie query expression
            return getattr(owner, self.target_attr)
        # Called on instance (e.g. group.item_id) -> returns integer value
        return getattr(instance, self.target_attr)


class BaseMongoDoc(Document):
    """Abstract base document with Mongoose-compatible configuration and timestamps."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="createdAt",
    )
    updated_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="updatedAt",
    )

    @model_validator(mode="before")
    @classmethod
    def _preprocess_incoming_data(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # 1. Remap domain integer ID aliased to 'id' (group_id, teacher_id, etc.)
        for field_name, field in cls.model_fields.items():
            if field.alias == "id" and field_name != "id":
                if "id" in data:
                    val = data.get("id")
                    # If it's an int or digit string and not an ObjectId, map to domain field
                    if isinstance(val, int) or (isinstance(val, str) and val.isdigit() and len(val) < 24):
                        data[field_name] = int(val)
                        data.pop("id")
                break

        # 2. Convert createdAt / updatedAt from None to default now if missing
        if "createdAt" in data and data["createdAt"] is None:
            data["createdAt"] = datetime.now(timezone.utc)
        if "updatedAt" in data and data["updatedAt"] is None:
            data["updatedAt"] = datetime.now(timezone.utc)

        return data

