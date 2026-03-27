"""Tests for the development-only auth sandbox."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.core.database import get_db
from papyrus.main import create_app
from papyrus.main import settings as app_settings


async def test_auth_sandbox_not_registered_in_production_mode(client: AsyncClient):
    """Test the auth sandbox is absent when debug mode is disabled."""
    response = await client.get("/__dev/auth-sandbox")
    assert response.status_code == 404


@pytest_asyncio.fixture
async def debug_client(
    monkeypatch: pytest.MonkeyPatch,
    test_session_maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    monkeypatch.setattr(app_settings, "debug", True)
    debug_app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_maker() as session:
            yield session

    debug_app.dependency_overrides[get_db] = override_get_db

    try:
        async with AsyncClient(transport=ASGITransport(app=debug_app), base_url="http://test") as client:
            yield client
    finally:
        debug_app.dependency_overrides.clear()


async def test_auth_sandbox_registered_in_debug_mode(debug_client: AsyncClient):
    """Test the auth sandbox page is served in debug mode."""
    response = await debug_client.get("/__dev/auth-sandbox")
    assert response.status_code == 200
    assert "Auth Sandbox" in response.text


async def test_auth_sandbox_session_endpoint(
    debug_client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
):
    """Test the auth sandbox session endpoint reports token and DB session state."""
    response = await debug_client.get("/__dev/auth-sandbox/session", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["user_id"] == auth_user["user_id"]
    assert body["session"]["session_id"] == auth_user["session_id"]
    assert body["access_payload"]["sub"] == auth_user["user_id"]
