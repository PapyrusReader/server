"""Session and token lifecycle service functions."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from papyrus.config import get_settings
from papyrus.core.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from papyrus.core.security import (
    create_access_token,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from papyrus.models import AuthSession, PasswordCredential, User
from papyrus.schemas.auth import LoginRequest, OAuthExchangeRequest, RefreshTokenRequest, RegisterRequest
from papyrus.services.auth._core import (
    _consume_exchange_code,
    _create_session_for_user,
    _expires_in_seconds,
    _get_active_user,
    _get_user_by_email,
    _normalize_email,
    _now,
    _revoke_user_sessions,
)
from papyrus.services.auth.types import AuthResult


async def register_user(
    session: AsyncSession,
    request: RegisterRequest,
    user_agent: str | None,
) -> AuthResult:
    normalized_email = _normalize_email(request.email)

    if await _get_user_by_email(session, normalized_email) is not None:
        raise ConflictError("An account with this email already exists")

    user = User(
        display_name=request.display_name,
        primary_email=normalized_email,
        primary_email_verified=False,
    )

    session.add(user)
    await session.flush()

    session.add(
        PasswordCredential(
            user_id=user.user_id,
            password_hash=hash_password(request.password),
        )
    )

    result = await _create_session_for_user(
        session,
        user,
        client_type=request.client_type,
        device_label=request.device_label,
        user_agent=user_agent,
    )

    await session.commit()
    await session.refresh(user)
    return result


async def login_user(
    session: AsyncSession,
    request: LoginRequest,
    user_agent: str | None,
) -> AuthResult:
    result = await session.execute(
        select(User)
        .options(selectinload(User.password_credential))
        .where(User.primary_email == _normalize_email(request.email))
    )

    user = result.scalar_one_or_none()

    if user is None or user.password_credential is None:
        raise UnauthorizedError("Invalid email or password")

    if user.disabled_at is not None:
        raise ForbiddenError("User account is disabled")

    if not verify_password(request.password, user.password_credential.password_hash):
        raise UnauthorizedError("Invalid email or password")

    auth_result = await _create_session_for_user(
        session,
        user,
        client_type=request.client_type,
        device_label=request.device_label,
        user_agent=user_agent,
    )

    await session.commit()
    await session.refresh(user)
    return auth_result


async def refresh_tokens(
    session: AsyncSession,
    request: RefreshTokenRequest,
) -> AuthResult:
    result = await session.execute(
        select(AuthSession)
        .options(selectinload(AuthSession.user))
        .where(AuthSession.refresh_token_hash == hash_opaque_token(request.refresh_token))
    )

    auth_session = result.scalar_one_or_none()

    if auth_session is None or auth_session.revoked_at is not None or auth_session.expires_at <= _now():
        raise UnauthorizedError("Refresh token is invalid or expired")

    user = auth_session.user

    if user.disabled_at is not None:
        raise ForbiddenError("User account is disabled")

    new_refresh_token = generate_opaque_token()
    auth_session.refresh_token_hash = hash_opaque_token(new_refresh_token)
    auth_session.expires_at = _now() + timedelta(days=get_settings().refresh_token_expire_days)
    auth_session.last_seen_at = _now()
    user.last_login_at = _now()
    await session.flush()
    access_token = create_access_token({"sub": str(user.user_id), "sid": str(auth_session.session_id)})
    await session.commit()
    await session.refresh(user)

    return AuthResult(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=_expires_in_seconds(),
        user=user,
    )


async def logout_current_session(session: AsyncSession, user_id: UUID, session_id: UUID | None) -> None:
    if session_id is None:
        await _revoke_user_sessions(session, user_id)
        await session.commit()
        return

    auth_session = await session.get(AuthSession, session_id)

    if auth_session is not None and auth_session.user_id == user_id and auth_session.revoked_at is None:
        auth_session.revoked_at = _now()
        await session.commit()


async def logout_all_sessions(session: AsyncSession, user_id: UUID) -> None:
    await _revoke_user_sessions(session, user_id)
    await session.commit()


async def exchange_login_code(
    session: AsyncSession,
    request: OAuthExchangeRequest,
    user_agent: str | None,
) -> AuthResult:
    exchange_code = await _consume_exchange_code(session, request.code, purpose="login")

    if exchange_code.user_id is None:
        raise UnauthorizedError("Exchange code is missing a user")

    user = await _get_active_user(session, exchange_code.user_id)

    auth_result = await _create_session_for_user(
        session,
        user,
        client_type=request.client_type,
        device_label=request.device_label,
        user_agent=user_agent,
    )

    await session.commit()
    await session.refresh(user)
    return auth_result
