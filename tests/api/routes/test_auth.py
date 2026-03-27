"""Tests for authentication endpoints."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core import security as security_module
from papyrus.core.security import generate_opaque_token, hash_opaque_token
from papyrus.models import EmailActionToken, User, UserIdentity
from papyrus.services import auth as auth_service


@pytest.fixture
def configured_google(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "google-client-secret")


@pytest.fixture
def configured_powersync(monkeypatch: pytest.MonkeyPatch) -> bytes:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    settings = get_settings()
    monkeypatch.setattr(settings, "powersync_jwt_private_key", private_pem)
    monkeypatch.setattr(settings, "powersync_jwt_public_key", public_pem.decode("utf-8"))
    monkeypatch.setattr(settings, "powersync_jwt_audience", "https://powersync.example.test")

    security_module._get_powersync_private_key.cache_clear()
    security_module._get_powersync_public_key.cache_clear()
    yield public_pem
    security_module._get_powersync_private_key.cache_clear()
    security_module._get_powersync_public_key.cache_clear()


async def test_register_user_returns_tokens(client: AsyncClient):
    """Test user registration endpoint."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
            "client_type": "mobile",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "test@example.com"
    assert data["user"]["display_name"] == "Test User"


async def test_register_duplicate_email_returns_conflict(client: AsyncClient):
    """Test duplicate registrations are rejected."""
    payload = {
        "email": "test@example.com",
        "password": "SecureP@ss123",
        "display_name": "Test User",
    }
    first_response = await client.post("/v1/auth/register", json=payload)
    assert first_response.status_code == 201

    second_response = await client.post("/v1/auth/register", json=payload)
    assert second_response.status_code == 409


async def test_login_user(client: AsyncClient):
    """Test user login endpoint."""
    register_response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
        },
    )
    assert register_response.status_code == 201

    response = await client.post(
        "/v1/auth/login",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "client_type": "desktop",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] > 0


async def test_login_user_rejects_invalid_password(client: AsyncClient):
    """Test invalid passwords are rejected."""
    register_response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
        },
    )
    assert register_response.status_code == 201

    response = await client.post(
        "/v1/auth/login",
        json={
            "email": "test@example.com",
            "password": "WrongPassword123",
        },
    )
    assert response.status_code == 401


async def test_refresh_token_rotates_and_invalidates_previous_token(client: AsyncClient):
    """Test refresh token rotation endpoint."""
    register_response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
        },
    )
    assert register_response.status_code == 201
    refresh_token = register_response.json()["refresh_token"]

    refresh_response = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_response.status_code == 200
    rotated_refresh_token = refresh_response.json()["refresh_token"]
    assert rotated_refresh_token != refresh_token

    invalidated_response = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert invalidated_response.status_code == 401


async def test_logout_current_session_revokes_refresh_token(client: AsyncClient):
    """Test user logout revokes the current session refresh token."""
    register_response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
        },
    )
    assert register_response.status_code == 201
    auth_payload = register_response.json()

    logout_response = await client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {auth_payload['access_token']}"},
        json={"all_devices": False},
    )
    assert logout_response.status_code == 204

    refresh_response = await client.post("/v1/auth/refresh", json={"refresh_token": auth_payload["refresh_token"]})
    assert refresh_response.status_code == 401


async def test_logout_all_revokes_other_sessions(client: AsyncClient):
    """Test logout-all revokes all refresh-token backed sessions."""
    register_response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
        },
    )
    assert register_response.status_code == 201
    first_auth = register_response.json()

    login_response = await client.post(
        "/v1/auth/login",
        json={"email": "test@example.com", "password": "SecureP@ss123"},
    )
    assert login_response.status_code == 200
    second_auth = login_response.json()

    logout_all_response = await client.post(
        "/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {first_auth['access_token']}"},
    )
    assert logout_all_response.status_code == 204

    first_refresh_response = await client.post("/v1/auth/refresh", json={"refresh_token": first_auth["refresh_token"]})
    second_refresh_response = await client.post(
        "/v1/auth/refresh", json={"refresh_token": second_auth["refresh_token"]}
    )
    assert first_refresh_response.status_code == 401
    assert second_refresh_response.status_code == 401


async def test_google_oauth_flow_creates_user_and_exchanges_code(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    configured_google: None,
):
    """Test the browser-based Google OAuth flow."""
    monkeypatch.setattr(
        auth_service.google_oauth_client,
        "exchange_code_for_identity",
        lambda code, callback_uri: auth_service.GoogleIdentity(
            subject="google-sub-1",
            email="google_user@example.com",
            email_verified=True,
            display_name="Google User",
            avatar_url="https://example.com/avatar.png",
        ),
    )

    start_response = await client.get(
        "/v1/auth/oauth/google/start",
        params={"redirect_uri": "papyrus://auth/callback"},
        follow_redirects=False,
    )
    assert start_response.status_code == 302
    start_location = start_response.headers["location"]
    state = parse_qs(urlparse(start_location).query)["state"][0]

    callback_response = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"code": "google-auth-code", "state": state},
        follow_redirects=False,
    )
    assert callback_response.status_code == 302
    redirect_location = callback_response.headers["location"]
    exchange_code = parse_qs(urlparse(redirect_location).query)["code"][0]

    exchange_response = await client.post(
        "/v1/auth/exchange-code",
        json={"code": exchange_code, "client_type": "web"},
    )
    assert exchange_response.status_code == 200
    data = exchange_response.json()
    assert data["user"]["email"] == "google_user@example.com"
    assert data["user"]["display_name"] == "Google User"


async def test_google_oauth_does_not_auto_link_existing_email(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    configured_google: None,
):
    """Test Google login does not auto-link to an existing email/password account."""
    register_response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
        },
    )
    assert register_response.status_code == 201

    monkeypatch.setattr(
        auth_service.google_oauth_client,
        "exchange_code_for_identity",
        lambda code, callback_uri: auth_service.GoogleIdentity(
            subject="google-sub-2",
            email="test@example.com",
            email_verified=True,
            display_name="Google User",
            avatar_url=None,
        ),
    )

    start_response = await client.get(
        "/v1/auth/oauth/google/start",
        params={"redirect_uri": "papyrus://auth/callback"},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start_response.headers["location"]).query)["state"][0]

    callback_response = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"code": "google-auth-code", "state": state},
        follow_redirects=False,
    )
    assert callback_response.status_code == 302
    redirect_location = callback_response.headers["location"]
    assert parse_qs(urlparse(redirect_location).query)["error"][0] == "account_exists"


async def test_google_link_flow_links_identity_to_existing_user(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    configured_google: None,
    db_session: AsyncSession,
):
    """Test explicit Google account linking for an authenticated user."""
    monkeypatch.setattr(
        auth_service.google_oauth_client,
        "exchange_code_for_identity",
        lambda code, callback_uri: auth_service.GoogleIdentity(
            subject="google-sub-link",
            email="linked@example.com",
            email_verified=True,
            display_name="Linked Google User",
            avatar_url="https://example.com/linked.png",
        ),
    )

    start_response = await client.post(
        "/v1/auth/link/google/start",
        headers=auth_headers,
        json={"redirect_uri": "papyrus://auth/callback"},
    )
    assert start_response.status_code == 200
    authorization_url = start_response.json()["authorization_url"]
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    callback_response = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"code": "google-link-code", "state": state},
        follow_redirects=False,
    )
    assert callback_response.status_code == 302
    exchange_code = parse_qs(urlparse(callback_response.headers["location"]).query)["code"][0]

    complete_response = await client.post(
        "/v1/auth/link/google/complete",
        headers=auth_headers,
        json={"code": exchange_code},
    )
    assert complete_response.status_code == 200

    identity_result = await db_session.execute(
        select(UserIdentity).where(UserIdentity.provider_subject == "google-sub-link")
    )
    identity = identity_result.scalar_one()
    assert str(identity.user_id) == auth_user["user_id"]


async def test_powersync_token_contains_expected_claims(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    configured_powersync: bytes,
):
    """Test PowerSync token minting."""
    response = await client.post("/v1/auth/powersync-token", headers=auth_headers)
    assert response.status_code == 200
    token = response.json()["token"]
    payload = jwt.decode(
        token,
        configured_powersync,
        algorithms=["RS256"],
        audience="https://powersync.example.test",
    )
    assert payload["sub"] == auth_user["user_id"]
    assert payload["type"] == "powersync"


async def test_powersync_jwks_returns_signing_key(
    client: AsyncClient,
    configured_powersync: bytes,
):
    """Test the PowerSync JWKS endpoint."""
    response = await client.get("/v1/auth/jwks")
    assert response.status_code == 200
    body = response.json()
    assert "keys" in body
    assert len(body["keys"]) == 1


async def test_verify_email(client: AsyncClient, db_session: AsyncSession):
    """Test email verification endpoint."""
    user = User(display_name="Needs Verification", primary_email="verify@example.com")
    db_session.add(user)
    await db_session.flush()

    plain_token = generate_opaque_token()
    db_session.add(
        EmailActionToken(
            user_id=user.user_id,
            action_type=auth_service.EMAIL_VERIFICATION_ACTION,
            token_hash=hash_opaque_token(plain_token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    response = await client.post("/v1/auth/verify-email", json={"token": plain_token})
    assert response.status_code == 200
    assert response.json()["message"] == "Email verified successfully"


async def test_forgot_password_returns_configuration_message(client: AsyncClient):
    """Test forgot password endpoint without SMTP configuration."""
    register_response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
        },
    )
    assert register_response.status_code == 201

    response = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "test@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Password reset is not configured on this server"


async def test_reset_password(client: AsyncClient, db_session: AsyncSession):
    """Test password reset endpoint."""
    user = User(display_name="Resettable User", primary_email="reset@example.com")
    db_session.add(user)
    await db_session.flush()

    plain_token = generate_opaque_token()
    db_session.add(
        EmailActionToken(
            user_id=user.user_id,
            action_type=auth_service.PASSWORD_RESET_ACTION,
            token_hash=hash_opaque_token(plain_token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    response = await client.post(
        "/v1/auth/reset-password",
        json={
            "token": plain_token,
            "password": "NewSecureP@ss123",
        },
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Password has been reset successfully"
