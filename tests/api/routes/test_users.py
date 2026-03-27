"""Tests for user endpoints."""

from httpx import AsyncClient


async def test_get_current_user(client: AsyncClient, auth_headers: dict[str, str], auth_user: dict[str, str]):
    """Test getting current user profile."""
    response = await client.get("/v1/users/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == auth_user["user_id"]
    assert data["email"] == "user@example.com"
    assert data["display_name"] == "Example User"


async def test_update_current_user(client: AsyncClient, auth_headers: dict[str, str]):
    """Test updating current user profile."""
    response = await client.patch(
        "/v1/users/me",
        headers=auth_headers,
        json={"display_name": "Updated Name"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Updated Name"


async def test_delete_current_user(client: AsyncClient, auth_headers: dict[str, str], auth_user: dict[str, str]):
    """Test deleting current user account."""
    response = await client.request(
        "DELETE",
        "/v1/users/me",
        headers=auth_headers,
        json={"password": auth_user["password"]},
    )
    assert response.status_code == 204


async def test_get_user_preferences(client: AsyncClient, auth_headers: dict[str, str]):
    """Test getting user preferences."""
    response = await client.get("/v1/users/me/preferences", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "theme" in data


async def test_update_user_preferences(client: AsyncClient, auth_headers: dict[str, str]):
    """Test updating user preferences."""
    response = await client.put(
        "/v1/users/me/preferences",
        headers=auth_headers,
        json={"theme": "light", "notifications_enabled": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["theme"] == "light"
    assert data["notifications_enabled"] is False


async def test_change_password(client: AsyncClient, auth_headers: dict[str, str], auth_user: dict[str, str]):
    """Test changing user password."""
    response = await client.post(
        "/v1/users/me/change-password",
        headers=auth_headers,
        json={
            "current_password": auth_user["password"],
            "new_password": "NewSecureP@ss123",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "message" in data

    protected_response = await client.get("/v1/users/me", headers=auth_headers)
    assert protected_response.status_code == 401


async def test_unauthorized_access(client: AsyncClient):
    """Test that endpoints require authentication."""
    response = await client.get("/v1/users/me")
    assert response.status_code == 401  # No auth header
