"""Tests for authentication endpoints."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core import security as security_module
from papyrus.core.exceptions import ServiceUnavailableError
from papyrus.core.security import generate_opaque_token, hash_opaque_token
from papyrus.models import AuthExchangeCode, EmailActionToken, User, UserIdentity
from papyrus.services import auth as auth_service
from papyrus.services import email as email_service


@pytest.fixture
def configured_google(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "google-client-secret")


@pytest.fixture
def unconfigured_google(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", None)
    monkeypatch.setattr(settings, "google_oauth_client_secret", None)


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
    monkeypatch.setattr(settings, "powersync_jwt_private_key_file", None)
    monkeypatch.setattr(settings, "powersync_jwt_public_key", public_pem.decode("utf-8"))
    monkeypatch.setattr(settings, "powersync_jwt_public_key_file", None)
    monkeypatch.setattr(settings, "powersync_jwt_audience", "https://powersync.example.test")
    security_module._get_powersync_private_key.cache_clear()
    security_module._get_powersync_public_key.cache_clear()
    yield public_pem
    security_module._get_powersync_private_key.cache_clear()
    security_module._get_powersync_public_key.cache_clear()


@pytest.fixture
def unconfigured_powersync(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "powersync_jwt_private_key", None)
    monkeypatch.setattr(settings, "powersync_jwt_private_key_file", None)
    monkeypatch.setattr(settings, "powersync_jwt_public_key", None)
    monkeypatch.setattr(settings, "powersync_jwt_public_key_file", None)
    monkeypatch.setattr(settings, "powersync_jwt_audience", None)
    security_module._get_powersync_private_key.cache_clear()
    security_module._get_powersync_public_key.cache_clear()
    yield
    security_module._get_powersync_private_key.cache_clear()
    security_module._get_powersync_public_key.cache_clear()


@pytest.fixture
def configured_email_delivery(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_delivery_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "noreply@example.test")
    sent_messages: list[tuple[str, str, str]] = []

    def fake_send_email(recipient: str, subject: str, body: str) -> None:
        sent_messages.append((recipient, subject, body))

    monkeypatch.setattr(email_service, "send_email", fake_send_email)
    return sent_messages


@pytest.fixture
def unconfigured_email_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_delivery_enabled", False)
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "smtp_from_email", None)


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

    protected_response = await client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {auth_payload['access_token']}"},
    )
    assert protected_response.status_code == 401


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

    first_protected_response = await client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {first_auth['access_token']}"},
    )
    second_protected_response = await client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {second_auth['access_token']}"},
    )
    assert first_protected_response.status_code == 401
    assert second_protected_response.status_code == 401


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


async def test_google_oauth_start_requires_configuration(
    client: AsyncClient,
    unconfigured_google: None,
):
    """Test Google OAuth start returns a controlled error when not configured."""
    response = await client.get(
        "/v1/auth/oauth/google/start",
        params={"redirect_uri": "papyrus://auth/callback"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


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


async def test_powersync_token_requires_signing_configuration(
    client: AsyncClient,
    auth_headers: dict[str, str],
    unconfigured_powersync: None,
):
    """Test PowerSync token minting fails cleanly without signing config."""
    response = await client.post("/v1/auth/powersync-token", headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


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


async def test_verify_email_rejects_expired_token(client: AsyncClient, db_session: AsyncSession):
    """Test email verification rejects expired tokens."""
    user = User(display_name="Needs Verification", primary_email="verify@example.com")
    db_session.add(user)
    await db_session.flush()
    plain_token = generate_opaque_token()
    db_session.add(
        EmailActionToken(
            user_id=user.user_id,
            action_type=auth_service.EMAIL_VERIFICATION_ACTION,
            token_hash=hash_opaque_token(plain_token),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await db_session.commit()
    response = await client.post("/v1/auth/verify-email", json={"token": plain_token})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_resend_verification_sends_email_when_configured(
    client: AsyncClient,
    configured_email_delivery: list[tuple[str, str, str]],
):
    """Test resend verification sends an email when SMTP is configured."""
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
        "/v1/auth/resend-verification",
        json={"email": "test@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "If the email is registered, a verification link has been sent"
    assert len(configured_email_delivery) == 1
    recipient, subject, body = configured_email_delivery[0]
    assert recipient == "test@example.com"
    assert subject == "Verify your Papyrus email address"
    assert "Verification token:" in body


async def test_forgot_password_returns_configuration_message(
    client: AsyncClient,
    unconfigured_email_delivery: None,
):
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


async def test_forgot_password_sends_email_when_configured(
    client: AsyncClient,
    configured_email_delivery: list[tuple[str, str, str]],
):
    """Test forgot password issues a token and sends an email when SMTP is configured."""
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
    assert response.json()["message"] == "If the email is registered, a reset link has been sent"
    assert len(configured_email_delivery) == 1
    recipient, subject, body = configured_email_delivery[0]
    assert recipient == "test@example.com"
    assert subject == "Reset your Papyrus password"
    assert "Reset token:" in body


async def test_forgot_password_send_failure_returns_service_unavailable(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test SMTP failures surface as controlled app errors."""
    register_response = await client.post(
        "/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "SecureP@ss123",
            "display_name": "Test User",
        },
    )
    assert register_response.status_code == 201
    settings = get_settings()
    monkeypatch.setattr(settings, "email_delivery_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "noreply@example.test")
    monkeypatch.setattr(
        email_service,
        "send_email",
        lambda recipient, subject, body: (_ for _ in ()).throw(ServiceUnavailableError("Email delivery failed")),
    )

    response = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "test@example.com"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


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


async def test_reset_password_revokes_existing_access_token(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """Test password reset revokes any existing authenticated session."""
    plain_token = generate_opaque_token()
    db_session.add(
        EmailActionToken(
            user_id=UUID(auth_user["user_id"]),
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
    protected_response = await client.get("/v1/users/me", headers=auth_headers)
    assert protected_response.status_code == 401


async def test_reset_password_rejects_expired_token(client: AsyncClient, db_session: AsyncSession):
    """Test password reset rejects expired tokens."""
    user = User(display_name="Resettable User", primary_email="reset@example.com")
    db_session.add(user)
    await db_session.flush()
    plain_token = generate_opaque_token()
    db_session.add(
        EmailActionToken(
            user_id=user.user_id,
            action_type=auth_service.PASSWORD_RESET_ACTION,
            token_hash=hash_opaque_token(plain_token),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
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
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_exchange_code_rejects_expired_code(client: AsyncClient, db_session: AsyncSession):
    """Test OAuth exchange rejects expired codes."""
    user = User(display_name="OAuth User", primary_email="oauth@example.com")
    db_session.add(user)
    await db_session.flush()
    plain_code = generate_opaque_token()
    db_session.add(
        AuthExchangeCode(
            code_hash=hash_opaque_token(plain_code),
            purpose="login",
            user_id=user.user_id,
            provider="google",
            redirect_uri="papyrus://auth/callback",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await db_session.commit()

    response = await client.post(
        "/v1/auth/exchange-code",
        json={"code": plain_code, "client_type": "web"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_exchange_code_rejects_reused_code(client: AsyncClient, db_session: AsyncSession):
    """Test OAuth exchange rejects a code after it has been consumed."""
    user = User(display_name="OAuth User", primary_email="oauth@example.com")
    db_session.add(user)
    await db_session.flush()
    plain_code = generate_opaque_token()
    db_session.add(
        AuthExchangeCode(
            code_hash=hash_opaque_token(plain_code),
            purpose="login",
            user_id=user.user_id,
            provider="google",
            redirect_uri="papyrus://auth/callback",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            used_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    response = await client.post(
        "/v1/auth/exchange-code",
        json={"code": plain_code, "client_type": "web"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
