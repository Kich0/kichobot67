"""Pytest configuration and common fixtures."""

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient
from beanie import init_beanie

from src.database.models import ALL_DOCUMENT_MODELS
from src.database.connection import init_db, close_db


@pytest_asyncio.fixture
async def mock_db():
    """Initializes Beanie with mongomock_motor for safe, fast in-memory database testing."""
    client = AsyncMongoMockClient()
    db = client["kichobot_test"]
    await init_db(client=client, database_name="kichobot_test")
    yield db
    await close_db()
