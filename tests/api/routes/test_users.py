"""Tests for user endpoints."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.security import create_access_token, create_state_token
from papyrus.models import AuthSession, User


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

    protected_response = await client.get("/v1/users/me", headers=auth_headers)
    assert protected_response.status_code == 401


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


async def test_malformed_token_is_rejected(client: AsyncClient):
    """Test malformed bearer tokens are rejected."""
    response = await client.get("/v1/users/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_wrong_token_type_is_rejected(client: AsyncClient):
    """Test non-access JWTs are rejected."""
    token = create_state_token({"sub": "user", "sid": "session"})
    response = await client.get("/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_expired_access_token_is_rejected(client: AsyncClient, auth_user: dict[str, str]):
    """Test expired access tokens are rejected before DB session lookup."""
    expired_token = create_access_token(
        {"sub": auth_user["user_id"], "sid": auth_user["session_id"]},
        expires_delta=timedelta(seconds=-1),
    )
    response = await client.get("/v1/users/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 401


async def test_expired_db_session_is_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """Test an expired backing DB session revokes access immediately."""
    session = await db_session.get(AuthSession, auth_user["session_id"])
    assert session is not None
    session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    response = await client.get("/v1/users/me", headers=auth_headers)
    assert response.status_code == 401


async def test_disabled_user_is_rejected_on_protected_route(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """Test disabled users are blocked from protected routes."""
    user = await db_session.get(User, auth_user["user_id"])
    assert user is not None
    user.disabled_at = datetime.now(UTC)
    await db_session.commit()

    response = await client.get("/v1/users/me", headers=auth_headers)
    assert response.status_code == 403


async def test_unauthorized_access(client: AsyncClient):
    """Test that endpoints require authentication."""
    response = await client.get("/v1/users/me")
    assert response.status_code == 401  # No auth header
