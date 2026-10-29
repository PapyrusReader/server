import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from papyrus.config import get_settings
from papyrus.core.database import get_db
from papyrus.core.rate_limit import limiter
from papyrus.core.security import create_access_token, generate_opaque_token, hash_opaque_token, hash_password
from papyrus.main import create_app
from papyrus.main import settings as app_settings
from papyrus.models import AuthSession, Base, PasswordCredential, User


@pytest.fixture
def user_id() -> str:
    return str(uuid4())


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


async def rebuild_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture(scope="session")
async def test_schema(setup_test_db: None) -> AsyncGenerator[None, None]:
    engine = create_async_engine(TEST_DATABASE_URL)

    try:
        await rebuild_schema(engine)
        yield
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        await engine.dispose()


@asynccontextmanager
async def isolated_sessions(
    engine: AsyncEngine,
    *,
    migration: bool = False,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Preserve real transactions while removing committed state even after failure."""
    if migration:
        await rebuild_schema(engine)

    try:
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        if migration:
            await rebuild_schema(engine)
        else:
            tables = ", ".join(_quote_ident(table.name) for table in Base.metadata.sorted_tables)

            async with engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def test_session_maker(
    test_schema: None,
    request: pytest.FixtureRequest,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(TEST_DATABASE_URL)

    try:
        async with isolated_sessions(
            engine, migration=request.node.get_closest_marker("migration") is not None
        ) as maker:
            yield maker
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def public_client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """Exercise public routes without opening a database connection."""
    async with public_http_client(monkeypatch, debug=False) as client:
        yield client


@pytest_asyncio.fixture
async def public_debug_client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    async with public_http_client(monkeypatch, debug=True) as client:
        yield client


@asynccontextmanager
async def public_http_client(monkeypatch: pytest.MonkeyPatch, *, debug: bool) -> AsyncGenerator[AsyncClient, None]:
    monkeypatch.setattr(app_settings, "debug", debug)
    monkeypatch.setattr(app_settings, "public_base_url", "http://localhost:8080")
    monkeypatch.setattr(app_settings, "app_public_base_url", "http://papyrus.localhost:3000")
    app = create_app()

    async def reject_database_access() -> AsyncGenerator[AsyncSession, None]:
        raise AssertionError("Use a database-backed client for routes that access the database")
        yield

    app.dependency_overrides[get_db] = reject_database_access

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def prod_client(
    monkeypatch: pytest.MonkeyPatch,
    test_session_maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    monkeypatch.setattr(app_settings, "debug", False)
    monkeypatch.setattr(app_settings, "public_base_url", "http://localhost:8080")
    monkeypatch.setattr(app_settings, "app_public_base_url", "http://papyrus.localhost:3000")
    prod_app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_maker() as session:
            yield session

    prod_app.dependency_overrides[get_db] = override_get_db

    try:
        async with AsyncClient(transport=ASGITransport(app=prod_app), base_url="http://test") as c:
            yield c
    finally:
        prod_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def debug_client(
    monkeypatch: pytest.MonkeyPatch,
    test_session_maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    monkeypatch.setattr(app_settings, "debug", True)
    monkeypatch.setattr(app_settings, "public_base_url", "http://localhost:8080")
    monkeypatch.setattr(app_settings, "app_public_base_url", "http://papyrus.localhost:3000")
    debug_app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_maker() as session:
            yield session

    debug_app.dependency_overrides[get_db] = override_get_db

    try:
        async with AsyncClient(transport=ASGITransport(app=debug_app), base_url="http://test") as c:
            yield c
    finally:
        debug_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(prod_client: AsyncClient) -> AsyncGenerator[AsyncClient, None]:
    yield prod_client


@pytest_asyncio.fixture
async def db_session(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with test_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def auth_user(
    test_session_maker: async_sessionmaker[AsyncSession],
    fixture_password_hash: str,
) -> dict[str, str]:
    user_uuid = uuid4()
    refresh_token = generate_opaque_token()

    async with test_session_maker() as session:
        user = User(
            user_id=user_uuid,
            display_name="Example User",
            primary_email="user@example.com",
            primary_email_verified=True,
            last_login_at=datetime.now(UTC),
        )

        session.add(user)
        session.add(PasswordCredential(user_id=user_uuid, password_hash=fixture_password_hash))
        await session.flush()

        auth_session = AuthSession(
            user_id=user_uuid,
            refresh_token_hash=hash_opaque_token(refresh_token),
            client_type="test",
            device_label="pytest",
            expires_at=datetime.now(UTC) + timedelta(days=30),
            last_seen_at=datetime.now(UTC),
        )

        session.add(auth_session)
        await session.commit()
        access_token = create_access_token({"sub": str(user_uuid), "sid": str(auth_session.session_id)})

    return {
        "user_id": str(user_uuid),
        "session_id": str(auth_session.session_id),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "password": "current_password",
    }


@pytest_asyncio.fixture
async def auth_headers(auth_user: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_user['access_token']}"}


@pytest.fixture(scope="session")
def fixture_password_hash() -> str:
    return hash_password("current_password")


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Keep per-IP rate-limit state isolated between tests."""
    limiter._storage.reset()
