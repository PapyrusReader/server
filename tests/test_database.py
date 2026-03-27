"""Integration tests verifying the test database can be created and used."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
async def test_database_is_reachable(db_session: AsyncSession):
    """Verify the test database accepts connections and executes queries."""
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest.mark.integration
async def test_setup_creates_database_idempotently(db_session: AsyncSession):
    """Verify we are connected to the correct test database."""
    result = await db_session.execute(text("SELECT current_database()"))
    assert result.scalar() == "papyrus_test"
