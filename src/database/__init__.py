"""Database module for MongoDB using Beanie ODM and Motor."""

from src.database.base import BaseMongoDoc
from src.database.connection import init_db, close_db, get_client, get_db

__all__ = ["BaseMongoDoc", "init_db", "close_db", "get_client", "get_db"]
