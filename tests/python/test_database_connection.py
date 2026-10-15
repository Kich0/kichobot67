"""Unit tests for database connection manager."""

import pytest
from mongomock_motor import AsyncMongoMockClient
from src.database.connection import init_db, close_db, get_client, get_db
from src.database.models import User, ALL_DOCUMENT_MODELS


@pytest.mark.asyncio
async def test_init_db_and_close_db():
    """Verifies that init_db properly configures client, database, and Beanie models."""
    client = AsyncMongoMockClient()
    db = await init_db(client=client, database_name="test_conn_db")

    assert get_client() is client
    assert get_db() is db
    assert db.name == "test_conn_db"

    # Verify a document operation works through connection
    user = User(userId=999, username="conn_tester")
    await user.insert()
    assert (await User.find_one(User.user_id == 999)) is not None

    await close_db()
    assert get_client() is None
    assert get_db() is None
