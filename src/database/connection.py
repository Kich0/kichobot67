"""MongoDB connection and Beanie ODM initialization manager.

Configured with Motor async connection pooling (maxPoolSize=10, minPoolSize=1)
optimized for Render Free tier 512 MB memory constraints.
"""

from typing import Any
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from beanie import init_beanie
from pymongo.errors import PyMongoError

from src.core.config import settings
from src.core.logging import logger
from src.database.models import ALL_DOCUMENT_MODELS

_client: AsyncIOMotorClient | None = None
_database: AsyncIOMotorDatabase | None = None


def get_client() -> AsyncIOMotorClient | None:
    """Returns the active Motor client instance, or None if not connected."""
    return _client


def get_db() -> AsyncIOMotorDatabase | None:
    """Returns the active Motor database instance, or None if not connected."""
    return _database


async def init_db(
    uri: str | None = None,
    database_name: str | None = None,
    client: Any = None,
) -> AsyncIOMotorDatabase:
    """Initializes MongoDB connection and registers all Beanie document models.

    Args:
        uri: Optional MongoDB connection URI (defaults to settings.DB_URI)
        database_name: Optional database name override (defaults to URI database or 'kichobot')
        client: Optional pre-configured client (e.g. AsyncMongoMockClient for tests)

    Returns:
        AsyncIOMotorDatabase: Connected and initialized database instance
    """
    global _client, _database

    connection_uri = uri or settings.DB_URI

    if client is not None:
        _client = client
        if database_name:
            _database = _client[database_name]
        else:
            try:
                _database = _client.get_default_database()
            except Exception:
                _database = _client["kichobot"]
    else:
        logger.info("Connecting to MongoDB via Motor async driver...")
        _client = AsyncIOMotorClient(
            connection_uri,
            maxPoolSize=10,
            minPoolSize=1,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
        )
        if database_name:
            _database = _client[database_name]
        else:
            try:
                _database = _client.get_default_database()
            except Exception:
                _database = _client["kichobot"]

    logger.info(f"Initializing Beanie with {len(ALL_DOCUMENT_MODELS)} models on database '{_database.name}'...")
    await init_beanie(database=_database, document_models=ALL_DOCUMENT_MODELS)
    logger.info("MongoDB and Beanie initialized successfully.")

    return _database


async def close_db() -> None:
    """Closes all active MongoDB connections and resets client state."""
    global _client, _database
    if _client is not None:
        logger.info("Closing MongoDB connection pool...")
        _client.close()
        _client = None
        _database = None
        logger.info("MongoDB connection closed.")
