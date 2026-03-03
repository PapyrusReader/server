import os

os.environ.setdefault("SECRET_KEY", "test-only-secret-key-do-not-use-in-production")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "test")

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://papyrus:papyrus@localhost:5432/papyrus_test",
)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_maker() as session:
        async with session.begin():
            yield session
            await session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
