import os

os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from papyrus.core.security import create_access_token
from papyrus.main import app


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
