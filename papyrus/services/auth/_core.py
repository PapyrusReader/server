"""Shared internal helpers for the auth service package."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError, ValidationError
from papyrus.core.security import (
    create_access_token,
    generate_opaque_token,
    hash_opaque_token,
)
from papyrus.models import AuthExchangeCode, AuthSession, EmailActionToken, User
from papyrus.services.auth.types import AuthResult, GoogleIdentity


def _now() -> datetime:
    return datetime.now(UTC)


def _expires_in_seconds() -> int:
    return get_settings().access_token_expire_minutes * 60


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _build_redirect_uri(base_uri: str, params: dict[str, str]) -> str:
    split = urlsplit(base_uri)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _get_client_metadata(client_type: str, device_label: str | None, user_agent: str | None) -> tuple[str, str | None]:
    normalized_client_type = client_type or "unknown"
    normalized_device_label = device_label or user_agent

    if normalized_device_label is not None:
        normalized_device_label = normalized_device_label[:255]

    return normalized_client_type, normalized_device_label


async def _create_session_for_user(
    session: AsyncSession,
    user: User,
    client_type: str,
    device_label: str | None,
    user_agent: str | None,
) -> AuthResult:
    refresh_token = generate_opaque_token()
    refresh_token_hash = hash_opaque_token(refresh_token)
    normalized_client_type, normalized_device_label = _get_client_metadata(client_type, device_label, user_agent)

    auth_session = AuthSession(
        user_id=user.user_id,
        refresh_token_hash=refresh_token_hash,
        client_type=normalized_client_type,
        device_label=normalized_device_label,
        expires_at=_now() + timedelta(days=get_settings().refresh_token_expire_days),
        last_seen_at=_now(),
    )

    session.add(auth_session)
    user.last_login_at = _now()
    await session.flush()
    access_token = create_access_token({"sub": str(user.user_id), "sid": str(auth_session.session_id)})

    return AuthResult(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=_expires_in_seconds(),
        user=user,
    )


async def _get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.primary_email == _normalize_email(email)))
    return result.scalar_one_or_none()


async def _get_active_user(session: AsyncSession, user_id: UUID) -> User:
    user = await session.get(User, user_id)

    if user is None:
        raise NotFoundError("User not found")

    if user.disabled_at is not None:
        raise ForbiddenError("User account is disabled")

    return user


async def _revoke_user_sessions(session: AsyncSession, user_id: UUID) -> None:
    result = await session.execute(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    )

    for auth_session in result.scalars():
        auth_session.revoked_at = _now()


async def _create_exchange_code(
    session: AsyncSession,
    *,
    purpose: str,
    redirect_uri: str,
    user_id: UUID | None = None,
    provider: str | None = None,
    provider_subject: str | None = None,
    email_at_provider: str | None = None,
    email_verified_at_provider: datetime | None = None,
    display_name: str | None = None,
    avatar_url: str | None = None,
) -> str:
    plain_code = generate_opaque_token()

    session.add(
        AuthExchangeCode(
            code_hash=hash_opaque_token(plain_code),
            purpose=purpose,
            user_id=user_id,
            provider=provider,
            provider_subject=provider_subject,
            email_at_provider=email_at_provider,
            email_verified_at_provider=email_verified_at_provider,
            display_name=display_name,
            avatar_url=avatar_url,
            redirect_uri=redirect_uri,
            expires_at=_now() + timedelta(minutes=get_settings().auth_exchange_code_expire_minutes),
        )
    )

    await session.flush()
    return plain_code


async def _consume_exchange_code(session: AsyncSession, code: str, purpose: str) -> AuthExchangeCode:
    result = await session.execute(
        select(AuthExchangeCode).where(
            AuthExchangeCode.code_hash == hash_opaque_token(code),
            AuthExchangeCode.purpose == purpose,
            AuthExchangeCode.used_at.is_(None),
        )
    )
    exchange_code = result.scalar_one_or_none()

    if exchange_code is None or exchange_code.expires_at <= _now():
        raise UnauthorizedError("Exchange code is invalid or expired")

    exchange_code.used_at = _now()
    await session.flush()
    return exchange_code


async def _issue_email_action_token(
    session: AsyncSession,
    user_id: UUID,
    action_type: str,
    expires_minutes: int,
) -> str:
    plain_token = generate_opaque_token()

    session.add(
        EmailActionToken(
            user_id=user_id,
            action_type=action_type,
            token_hash=hash_opaque_token(plain_token),
            expires_at=_now() + timedelta(minutes=expires_minutes),
        )
    )

    await session.flush()
    return plain_token


async def _consume_email_action_token(session: AsyncSession, token: str, action_type: str) -> EmailActionToken:
    result = await session.execute(
        select(EmailActionToken).where(
            EmailActionToken.token_hash == hash_opaque_token(token),
            EmailActionToken.action_type == action_type,
            EmailActionToken.used_at.is_(None),
        )
    )
    email_token = result.scalar_one_or_none()

    if email_token is None or email_token.expires_at <= _now():
        raise ValidationError("Token is invalid or expired")

    email_token.used_at = _now()
    await session.flush()
    return email_token


def _default_display_name(identity: GoogleIdentity | None = None, email: str | None = None) -> str:
    if identity is not None and identity.display_name:
        return identity.display_name

    if email:
        return email.split("@", 1)[0]

    return "Papyrus User"


def _build_api_url(path: str) -> str | None:
    public_base_url = get_settings().public_base_url

    if public_base_url is None:
        return None

    base = public_base_url.rstrip("/")
    prefix = get_settings().api_prefix
    return f"{base}{prefix}{path}"


def _verification_email_body(token: str) -> str:
    verify_url = _build_api_url("/auth/verify-email")
    lines = [
        "Verify your Papyrus email address.",
        "",
        f"Verification token: {token}",
    ]

    if verify_url is not None:
        lines.extend(
            [
                "",
                f"Submit this token to: {verify_url}",
            ]
        )

    return "\n".join(lines)


def _password_reset_email_body(token: str) -> str:
    reset_url = _build_api_url("/auth/reset-password")
    lines = [
        "Use this token to reset your Papyrus password.",
        "",
        f"Reset token: {token}",
    ]

    if reset_url is not None:
        lines.extend(
            [
                "",
                f"Submit this token and your new password to: {reset_url}",
            ]
        )

    return "\n".join(lines)
