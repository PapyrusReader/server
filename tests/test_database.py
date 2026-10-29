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
async def test_setup_creates_database_idempotently(db_session: AsyncSession, test_database_name: str):
    """Verify we are connected to the correct test database."""
    result = await db_session.execute(text("SELECT current_database()"))
    assert result.scalar() == test_database_name


async def test_session_cleanup_removes_committed_rows_after_failure(test_schema):
    from sqlalchemy.ext.asyncio import create_async_engine

    from papyrus.models import User
    from tests.conftest import TEST_DATABASE_URL, isolated_sessions

    engine = create_async_engine(TEST_DATABASE_URL)

    try:
        with pytest.raises(RuntimeError, match="interrupted test"):
            async with isolated_sessions(engine) as sessions:
                async with sessions() as writer:
                    writer.add(User(display_name="Committed", primary_email="isolation@example.com"))
                    await writer.commit()

                async with sessions() as observer:
                    assert await observer.scalar(text("SELECT count(*) FROM users")) == 1

                raise RuntimeError("interrupted test")

        async with isolated_sessions(engine) as sessions, sessions() as observer:
            assert await observer.scalar(text("SELECT count(*) FROM users")) == 0
    finally:
        await engine.dispose()


async def test_migration_cleanup_restores_schema_after_committed_failure(test_schema):
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.conftest import TEST_DATABASE_URL, isolated_sessions

    engine = create_async_engine(TEST_DATABASE_URL)

    try:
        with pytest.raises(RuntimeError, match="interrupted migration"):
            async with isolated_sessions(engine, migration=True) as sessions:
                async with sessions() as session:
                    await session.execute(text("ALTER TABLE users DROP COLUMN display_name"))
                    await session.commit()

                raise RuntimeError("interrupted migration")

        async with isolated_sessions(engine) as sessions, sessions() as observer:
            assert (await observer.execute(text("SELECT display_name FROM users"))).all() == []
    finally:
        await engine.dispose()
