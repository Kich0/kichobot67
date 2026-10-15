"""User toll / paid feature credits document model."""

from typing import Any
from pymongo import IndexModel, ASCENDING
from pydantic import Field, field_validator
from src.database.base import BaseMongoDoc


class UserToll(BaseMongoDoc):
    """Paid access and star pricing configuration per user."""

    user_id: int = Field(alias="userId")
    username: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    star_price: int = Field(default=9000, alias="starPrice")
    paid_credits: int = Field(default=0, alias="paidCredits")
    added_by: int | None = Field(default=None, alias="addedBy")

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, v: Any) -> bool:
        if v is None:
            return True
        return bool(v)

    @field_validator("star_price", mode="before")
    @classmethod
    def _coerce_star_price(cls, v: Any) -> int:
        if v is None or v == "":
            return 9000
        return int(v)

    @field_validator("paid_credits", mode="before")
    @classmethod
    def _coerce_paid_credits(cls, v: Any) -> int:
        if v is None or v == "":
            return 0
        return int(v)

    @field_validator("added_by", mode="before")
    @classmethod
    def _coerce_added_by(cls, v: Any) -> Any:
        if v == "" or v is None:
            return None
        return v

    class Settings:
        name = "usertolls"
        indexes = [
            IndexModel([("userId", ASCENDING)], unique=True),
        ]
