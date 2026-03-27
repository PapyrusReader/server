"""Tests for the development-only auth sandbox."""

from httpx import AsyncClient


async def test_auth_sandbox_not_registered_in_production_mode(prod_client: AsyncClient):
    """Test the auth sandbox is absent when debug mode is disabled."""
    response = await prod_client.get("/__dev/auth-sandbox")
    assert response.status_code == 404


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
