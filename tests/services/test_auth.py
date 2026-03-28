"""Service-layer tests for authentication logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.config import get_settings
from papyrus.core import security as security_module
from papyrus.core.exceptions import ConflictError, ForbiddenError, UnauthorizedError, ValidationError
from papyrus.core.security import generate_opaque_token, hash_opaque_token, hash_password
from papyrus.models import AuthExchangeCode, AuthSession, EmailActionToken, PasswordCredential, User, UserIdentity
from papyrus.schemas.auth import LoginRequest, OAuthExchangeRequest, RefreshTokenRequest, RegisterRequest
from papyrus.services import auth as auth_service


def test_auth_package_facade_exports_public_surface() -> None:
    """The auth package façade preserves the legacy public surface."""
    assert auth_service.google_oauth_client is not None
    assert auth_service.GoogleIdentity.__name__ == "GoogleIdentity"
    assert auth_service.GOOGLE_PROVIDER == "google"
    assert callable(auth_service.register_user)


async def _create_user_with_password(
    session: AsyncSession,
    *,
    email: str,
    password: str = "SecureP@ss123",
    display_name: str = "Example User",
    disabled: bool = False,
) -> User:
    user = User(
        display_name=display_name,
        primary_email=email,
        primary_email_verified=True,
        disabled_at=datetime.now(UTC) if disabled else None,
    )
    session.add(user)
    await session.flush()
    session.add(PasswordCredential(user_id=user.user_id, password_hash=hash_password(password)))
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
def configured_google(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "google-client-secret")


@pytest.fixture
def configured_powersync(monkeypatch: pytest.MonkeyPatch) -> None:
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


async def test_register_user_creates_user_and_session(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test registration creates a user and backing auth session."""
    async with test_session_maker() as session:
        result = await auth_service.register_user(
            session,
            RegisterRequest(
                email="register@example.com",
                password="SecureP@ss123",
                display_name="Register User",
                client_type="web",
            ),
            "pytest",
        )

        session_result = await session.execute(select(AuthSession).where(AuthSession.user_id == result.user.user_id))
        auth_session = session_result.scalar_one()
        assert result.user.primary_email == "register@example.com"
        assert result.refresh_token
        assert auth_session.client_type == "web"
        assert auth_session.revoked_at is None


async def test_register_user_rejects_duplicate_email(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test duplicate email registration is rejected in the service layer."""
    async with test_session_maker() as session:
        await auth_service.register_user(
            session,
            RegisterRequest(
                email="duplicate@example.com",
                password="SecureP@ss123",
                display_name="First User",
            ),
            None,
        )

        with pytest.raises(ConflictError):
            await auth_service.register_user(
                session,
                RegisterRequest(
                    email="duplicate@example.com",
                    password="SecureP@ss123",
                    display_name="Second User",
                ),
                None,
            )


async def test_login_user_rejects_disabled_user(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test disabled accounts cannot log in."""
    async with test_session_maker() as session:
        await _create_user_with_password(session, email="disabled@example.com", disabled=True)

        with pytest.raises(ForbiddenError):
            await auth_service.login_user(
                session,
                LoginRequest(email="disabled@example.com", password="SecureP@ss123"),
                None,
            )


async def test_refresh_tokens_rotates_and_invalidates_previous_token(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test service-layer refresh token rotation invalidates the previous token."""
    async with test_session_maker() as session:
        register_result = await auth_service.register_user(
            session,
            RegisterRequest(
                email="refresh@example.com",
                password="SecureP@ss123",
                display_name="Refresh User",
            ),
            None,
        )
        first_refresh_token = register_result.refresh_token
        rotated = await auth_service.refresh_tokens(session, RefreshTokenRequest(refresh_token=first_refresh_token))
        assert rotated.refresh_token != first_refresh_token

        with pytest.raises(UnauthorizedError):
            await auth_service.refresh_tokens(session, RefreshTokenRequest(refresh_token=first_refresh_token))


async def test_logout_current_session_revokes_session(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test logout_current_session revokes only the targeted session."""
    async with test_session_maker() as session:
        result = await auth_service.register_user(
            session,
            RegisterRequest(
                email="logout@example.com",
                password="SecureP@ss123",
                display_name="Logout User",
            ),
            None,
        )
        session_result = await session.execute(select(AuthSession).where(AuthSession.user_id == result.user.user_id))
        auth_session = session_result.scalar_one()
        await auth_service.logout_current_session(session, result.user.user_id, auth_session.session_id)
        await session.refresh(auth_session)
        assert auth_session.revoked_at is not None


async def test_logout_all_sessions_revokes_every_session(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test logout_all_sessions revokes all sessions for the user."""
    async with test_session_maker() as session:
        await auth_service.register_user(
            session,
            RegisterRequest(
                email="all-sessions@example.com",
                password="SecureP@ss123",
                display_name="All Sessions User",
            ),
            None,
        )
        second_login = await auth_service.login_user(
            session,
            LoginRequest(email="all-sessions@example.com", password="SecureP@ss123"),
            None,
        )

        user_id = second_login.user.user_id
        await auth_service.logout_all_sessions(session, user_id)
        session_result = await session.execute(select(AuthSession).where(AuthSession.user_id == user_id))
        assert all(auth_session.revoked_at is not None for auth_session in session_result.scalars())


async def test_reset_password_revokes_all_sessions(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test password reset revokes active sessions."""
    async with test_session_maker() as session:
        register_result = await auth_service.register_user(
            session,
            RegisterRequest(
                email="reset@example.com",
                password="SecureP@ss123",
                display_name="Reset User",
            ),
            None,
        )
        plain_token = generate_opaque_token()
        session.add(
            EmailActionToken(
                user_id=register_result.user.user_id,
                action_type=auth_service.PASSWORD_RESET_ACTION,
                token_hash=hash_opaque_token(plain_token),
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        await session.commit()
        await auth_service.reset_password(session, plain_token, "NewSecureP@ss123")
        session_result = await session.execute(select(AuthSession).where(AuthSession.user_id == register_result.user.user_id))
        assert all(auth_session.revoked_at is not None for auth_session in session_result.scalars())


async def test_google_login_reuses_existing_identity(
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    configured_google: None,
):
    """Test Google login matches an existing linked identity by provider subject."""
    async with test_session_maker() as session:
        user = User(display_name="Google User", primary_email="google@example.com", primary_email_verified=True)
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.user_id,
                provider=auth_service.GOOGLE_PROVIDER,
                provider_subject="google-subject",
                email_at_provider="old@example.com",
            )
        )
        await session.commit()

        monkeypatch.setattr(
            auth_service.google_oauth_client,
            "exchange_code_for_identity",
            lambda code, callback_uri: auth_service.GoogleIdentity(
                subject="google-subject",
                email="updated@example.com",
                email_verified=True,
                display_name="Google User",
                avatar_url=None,
            ),
        )

        callback_uri = "https://server.example.test/v1/auth/oauth/google/callback"
        authorization_url = auth_service.build_google_login_authorization_url("papyrus://auth/callback", callback_uri)
        state = parse_qs(urlparse(authorization_url).query)["state"][0]

        redirect_url = await auth_service.handle_google_callback(
            session,
            callback_uri=callback_uri,
            code="google-code",
            state_token=state,
            error=None,
        )
        exchange_code = parse_qs(urlparse(redirect_url).query)["code"][0]

        result = await auth_service.exchange_login_code(
            session,
            OAuthExchangeRequest(code=exchange_code, client_type="web"),
            None,
        )
        identity_result = await session.execute(
            select(UserIdentity).where(UserIdentity.provider_subject == "google-subject")
        )
        identity = identity_result.scalar_one()
        assert result.user.user_id == user.user_id
        assert identity.email_at_provider == "updated@example.com"


async def test_google_login_does_not_auto_link_existing_email(
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    configured_google: None,
):
    """Test Google login returns account_exists when email matches an unlinked local user."""
    async with test_session_maker() as session:
        await _create_user_with_password(session, email="existing@example.com")

        monkeypatch.setattr(
            auth_service.google_oauth_client,
            "exchange_code_for_identity",
            lambda code, callback_uri: auth_service.GoogleIdentity(
                subject="new-google-sub",
                email="existing@example.com",
                email_verified=True,
                display_name="Existing Email",
                avatar_url=None,
            ),
        )

        callback_uri = "https://server.example.test/v1/auth/oauth/google/callback"
        authorization_url = auth_service.build_google_login_authorization_url("papyrus://auth/callback", callback_uri)
        state = parse_qs(urlparse(authorization_url).query)["state"][0]

        redirect_url = await auth_service.handle_google_callback(
            session,
            callback_uri=callback_uri,
            code="google-code",
            state_token=state,
            error=None,
        )

        assert parse_qs(urlparse(redirect_url).query)["error"][0] == "account_exists"


async def test_exchange_login_code_rejects_expired_code(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test expired exchange codes are rejected."""
    async with test_session_maker() as session:
        user = await _create_user_with_password(session, email="exchange-expired@example.com")
        plain_code = generate_opaque_token()
        session.add(
            AuthExchangeCode(
                code_hash=hash_opaque_token(plain_code),
                purpose="login",
                user_id=user.user_id,
                provider=auth_service.GOOGLE_PROVIDER,
                redirect_uri="papyrus://auth/callback",
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        await session.commit()

        with pytest.raises(UnauthorizedError):
            await auth_service.exchange_login_code(
                session,
                OAuthExchangeRequest(code=plain_code, client_type="web"),
                None,
            )


async def test_exchange_login_code_cannot_be_reused(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test exchange codes are one-time use."""
    async with test_session_maker() as session:
        user = await _create_user_with_password(session, email="exchange-reuse@example.com")
        plain_code = generate_opaque_token()
        session.add(
            AuthExchangeCode(
                code_hash=hash_opaque_token(plain_code),
                purpose="login",
                user_id=user.user_id,
                provider=auth_service.GOOGLE_PROVIDER,
                redirect_uri="papyrus://auth/callback",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.commit()

        result = await auth_service.exchange_login_code(
            session,
            OAuthExchangeRequest(code=plain_code, client_type="web"),
            None,
        )
        assert result.user.user_id == user.user_id

        with pytest.raises(UnauthorizedError):
            await auth_service.exchange_login_code(
                session,
                OAuthExchangeRequest(code=plain_code, client_type="web"),
                None,
            )


async def test_verify_email_token_rejects_expired_and_reused_tokens(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test email verification tokens cannot be expired or reused."""
    async with test_session_maker() as session:
        user = await _create_user_with_password(session, email="verify@example.com")
        expired_token = generate_opaque_token()
        valid_token = generate_opaque_token()
        session.add_all(
            [
                EmailActionToken(
                    user_id=user.user_id,
                    action_type=auth_service.EMAIL_VERIFICATION_ACTION,
                    token_hash=hash_opaque_token(expired_token),
                    expires_at=datetime.now(UTC) - timedelta(minutes=1),
                ),
                EmailActionToken(
                    user_id=user.user_id,
                    action_type=auth_service.EMAIL_VERIFICATION_ACTION,
                    token_hash=hash_opaque_token(valid_token),
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                ),
            ]
        )
        await session.commit()

        with pytest.raises(ValidationError):
            await auth_service.verify_email_token(session, expired_token)

        assert await auth_service.verify_email_token(session, valid_token) == "Email verified successfully"

        with pytest.raises(ValidationError):
            await auth_service.verify_email_token(session, valid_token)


async def test_create_powersync_credentials_requires_signing_config(
    configured_powersync: None,
):
    """Test PowerSync credentials fail cleanly without signing config."""
    with pytest.raises(ValidationError):
        await auth_service.create_powersync_credentials(UUID("00000000-0000-0000-0000-000000000001"))
