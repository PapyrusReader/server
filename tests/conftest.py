import os
from collections.abc import AsyncGenerator
from contextlib import suppress
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from papyrus.config import get_settings
from papyrus.core.security import create_access_token
from papyrus.main import app
from papyrus.models import Base


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def user_id() -> str:
    return str(uuid4())


@pytest.fixture
def auth_headers(user_id: str) -> dict[str, str]:
    token = create_access_token({"sub": user_id})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def book_id() -> str:
    return str(uuid4())


@pytest.fixture
def shelf_id() -> str:
    return str(uuid4())


@pytest.fixture
def tag_id() -> str:
    return str(uuid4())


@pytest.fixture
def series_id() -> str:
    return str(uuid4())


@pytest.fixture
def annotation_id() -> str:
    return str(uuid4())


@pytest.fixture
def note_id() -> str:
    return str(uuid4())


@pytest.fixture
def bookmark_id() -> str:
    return str(uuid4())


@pytest.fixture
def goal_id() -> str:
    return str(uuid4())


settings = get_settings()

ADMIN_POSTGRES_USER = settings.postgres_user
ADMIN_POSTGRES_PASSWORD = settings.postgres_password
ADMIN_POSTGRES_HOST = settings.postgres_host
ADMIN_POSTGRES_PORT = settings.postgres_port
TEST_DATABASE_NAME = os.environ.get("TEST_POSTGRES_DB", f"{settings.postgres_db}_test")

raw_test_database_url = os.environ.get("TEST_DATABASE_URL")

if raw_test_database_url:
    TEST_DATABASE_URL = make_url(raw_test_database_url)
else:
    TEST_DATABASE_URL = URL.create(
        drivername="postgresql+asyncpg",
        username=os.environ.get("TEST_POSTGRES_USER", settings.postgres_user),
        password=os.environ.get("TEST_POSTGRES_PASSWORD", settings.postgres_password),
        host=os.environ.get("TEST_POSTGRES_HOST", settings.postgres_host),
        port=int(os.environ.get("TEST_POSTGRES_PORT", str(settings.postgres_port))),
        database=TEST_DATABASE_NAME,
    )


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@pytest.fixture(scope="session")
def test_database_name() -> str:
    return TEST_DATABASE_URL.database or TEST_DATABASE_NAME


@pytest_asyncio.fixture(scope="session")
async def setup_test_db() -> None:
    """Create the test database and role if they do not exist."""
    test_db_user = TEST_DATABASE_URL.username
    test_db_password = TEST_DATABASE_URL.password
    test_db_name = TEST_DATABASE_URL.database

    if test_db_user is None or test_db_password is None or test_db_name is None:
        raise RuntimeError(
            "Test database configuration is incomplete. Provide credentials through .env or "
            "TEST_DATABASE_URL/TEST_POSTGRES_* overrides."
        )

    connection = await asyncpg.connect(
        user=ADMIN_POSTGRES_USER,
        password=ADMIN_POSTGRES_PASSWORD,
        host=ADMIN_POSTGRES_HOST,
        port=ADMIN_POSTGRES_PORT,
        database="postgres",
    )

    try:
        if test_db_user != ADMIN_POSTGRES_USER:
            with suppress(asyncpg.DuplicateObjectError):
                await connection.execute(
                    f"CREATE ROLE {_quote_ident(test_db_user)} WITH LOGIN PASSWORD {_quote_literal(test_db_password)}"
                )

        with suppress(asyncpg.DuplicateDatabaseError):
            await connection.execute(f"CREATE DATABASE {_quote_ident(test_db_name)} OWNER {_quote_ident(test_db_user)}")

        await connection.execute(
            f"GRANT ALL PRIVILEGES ON DATABASE {_quote_ident(test_db_name)} TO {_quote_ident(test_db_user)}"
        )
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def db_session(setup_test_db: None) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session, session.begin():
        yield session
        await session.rollback()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
