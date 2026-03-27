"""Authentication service layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import UUID

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from papyrus.config import get_settings
from papyrus.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError, ValidationError
from papyrus.core.security import (
    create_access_token,
    create_powersync_token,
    create_state_token,
    decode_state_token,
    generate_opaque_token,
    get_powersync_jwks,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from papyrus.models import AuthExchangeCode, AuthSession, EmailActionToken, PasswordCredential, User, UserIdentity
from papyrus.schemas.auth import LoginRequest, OAuthExchangeRequest, RefreshTokenRequest, RegisterRequest
from papyrus.services import email as email_service

GOOGLE_PROVIDER = "google"
EMAIL_VERIFICATION_ACTION = "verify_email"
PASSWORD_RESET_ACTION = "reset_password"
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


@dataclass(slots=True)
class AuthResult:
    """Authentication result returned by the service layer."""

    access_token: str
    refresh_token: str
    expires_in: int
    user: User


@dataclass(slots=True)
class GoogleIdentity:
    """Resolved Google identity from OAuth callback processing."""

    subject: str
    email: str | None
    email_verified: bool
    display_name: str | None
    avatar_url: str | None


class GoogleOAuthClient:
    """Google OAuth helper client."""

    def __init__(self) -> None:
        self._jwk_client = jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True)

    def build_authorization_url(self, callback_uri: str, state: str) -> str:
        settings = get_settings()
        if settings.google_oauth_client_id is None:
            raise ValidationError("Google OAuth is not configured")

        query = urlencode(
            {
                "client_id": settings.google_oauth_client_id,
                "redirect_uri": callback_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
            }
        )
        return f"{GOOGLE_AUTHORIZATION_URL}?{query}"

    def exchange_code_for_identity(self, code: str, callback_uri: str) -> GoogleIdentity:
        settings = get_settings()
        if settings.google_oauth_client_id is None or settings.google_oauth_client_secret is None:
            raise ValidationError("Google OAuth is not configured")

        payload = urlencode(
            {
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": callback_uri,
                "grant_type": "authorization_code",
            }
        ).encode("utf-8")

        request = Request(
            GOOGLE_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=10) as response:
                token_payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise ValidationError("Google OAuth exchange failed") from exc

        id_token = token_payload.get("id_token")
        if not isinstance(id_token, str):
            raise ValidationError("Google OAuth response did not include an ID token")

        signing_key = self._jwk_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.google_oauth_client_id,
            issuer=["accounts.google.com", "https://accounts.google.com"],
        )

        email = claims.get("email")
        return GoogleIdentity(
            subject=str(claims["sub"]),
            email=str(email).strip().lower() if isinstance(email, str) else None,
            email_verified=bool(claims.get("email_verified", False)),
            display_name=str(claims["name"]) if isinstance(claims.get("name"), str) else None,
            avatar_url=str(claims["picture"]) if isinstance(claims.get("picture"), str) else None,
        )


google_oauth_client = GoogleOAuthClient()


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


def _build_google_state(redirect_uri: str, mode: str, user_id: UUID | None = None) -> str:
    payload: dict[str, str] = {"redirect_uri": redirect_uri, "mode": mode}
    if user_id is not None:
        payload["user_id"] = str(user_id)
    return create_state_token(payload)


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
    session: AsyncSession, user_id: UUID, action_type: str, expires_minutes: int
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


def build_google_login_authorization_url(redirect_uri: str, callback_uri: str) -> str:
    state = _build_google_state(redirect_uri, mode="login")
    return google_oauth_client.build_authorization_url(callback_uri, state)


async def build_google_link_authorization_url(user_id: UUID, redirect_uri: str, callback_uri: str) -> str:
    return google_oauth_client.build_authorization_url(
        callback_uri,
        _build_google_state(redirect_uri, mode="link", user_id=user_id),
    )


async def _resolve_google_login_user(session: AsyncSession, identity: GoogleIdentity) -> tuple[User | None, str | None]:
    identity_result = await session.execute(
        select(UserIdentity)
        .options(selectinload(UserIdentity.user))
        .where(
            UserIdentity.provider == GOOGLE_PROVIDER,
            UserIdentity.provider_subject == identity.subject,
        )
    )
    existing_identity = identity_result.scalar_one_or_none()
    if existing_identity is not None:
        if existing_identity.user.disabled_at is not None:
            return None, "account_disabled"
        existing_identity.last_used_at = _now()
        existing_identity.email_at_provider = identity.email
        existing_identity.email_verified_at_provider = _now() if identity.email_verified else None
        return existing_identity.user, None

    if identity.email is not None:
        existing_user = await _get_user_by_email(session, identity.email)
        if existing_user is not None:
            return None, "account_exists"

    user = User(
        display_name=_default_display_name(identity, identity.email),
        avatar_url=identity.avatar_url,
        primary_email=identity.email,
        primary_email_verified=identity.email_verified,
        last_login_at=_now(),
    )
    session.add(user)
    await session.flush()

    session.add(
        UserIdentity(
            user_id=user.user_id,
            provider=GOOGLE_PROVIDER,
            provider_subject=identity.subject,
            email_at_provider=identity.email,
            email_verified_at_provider=_now() if identity.email_verified else None,
            last_used_at=_now(),
        )
    )
    await session.flush()
    return user, None


async def handle_google_callback(
    session: AsyncSession,
    *,
    callback_uri: str,
    code: str | None,
    state_token: str | None,
    error: str | None,
) -> str:
    state = decode_state_token(state_token or "")
    if state is None:
        raise ValidationError("OAuth state is invalid or expired")

    redirect_uri = state["redirect_uri"]
    if error is not None:
        return _build_redirect_uri(redirect_uri, {"error": error})
    if code is None:
        return _build_redirect_uri(redirect_uri, {"error": "missing_code"})

    identity = google_oauth_client.exchange_code_for_identity(code, callback_uri)
    mode = state.get("mode")

    if mode == "login":
        user, callback_error = await _resolve_google_login_user(session, identity)
        if callback_error is not None or user is None:
            await session.rollback()
            return _build_redirect_uri(redirect_uri, {"error": callback_error or "oauth_failed"})

        exchange_code = await _create_exchange_code(
            session,
            purpose="login",
            redirect_uri=redirect_uri,
            user_id=user.user_id,
        )
        await session.commit()
        return _build_redirect_uri(redirect_uri, {"code": exchange_code})

    if mode == "link":
        raw_user_id = state.get("user_id")
        if raw_user_id is None:
            raise ValidationError("OAuth state is missing link context")

        exchange_code = await _create_exchange_code(
            session,
            purpose="link_google",
            redirect_uri=redirect_uri,
            user_id=UUID(raw_user_id),
            provider=GOOGLE_PROVIDER,
            provider_subject=identity.subject,
            email_at_provider=identity.email,
            email_verified_at_provider=_now() if identity.email_verified else None,
            display_name=identity.display_name,
            avatar_url=identity.avatar_url,
        )
        await session.commit()
        return _build_redirect_uri(redirect_uri, {"code": exchange_code})

    raise ValidationError("OAuth flow mode is not supported")


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


async def complete_google_link(session: AsyncSession, user_id: UUID, code: str) -> User:
    exchange_code = await _consume_exchange_code(session, code, purpose="link_google")
    if exchange_code.user_id != user_id:
        raise ForbiddenError("Exchange code does not belong to the authenticated user")
    if exchange_code.provider_subject is None:
        raise ValidationError("Exchange code is missing provider identity details")

    user = await _get_active_user(session, user_id)

    identity_result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.provider == GOOGLE_PROVIDER,
            UserIdentity.provider_subject == exchange_code.provider_subject,
        )
    )
    existing_identity = identity_result.scalar_one_or_none()
    if existing_identity is not None:
        if existing_identity.user_id == user_id:
            return user
        raise ConflictError("This Google account is already linked to another user")

    session.add(
        UserIdentity(
            user_id=user.user_id,
            provider=GOOGLE_PROVIDER,
            provider_subject=exchange_code.provider_subject,
            email_at_provider=exchange_code.email_at_provider,
            email_verified_at_provider=exchange_code.email_verified_at_provider,
            last_used_at=_now(),
        )
    )

    if user.primary_email is None and exchange_code.email_at_provider is not None:
        user.primary_email = exchange_code.email_at_provider
        user.primary_email_verified = exchange_code.email_verified_at_provider is not None
    if user.avatar_url is None and exchange_code.avatar_url is not None:
        user.avatar_url = exchange_code.avatar_url

    await session.commit()
    await session.refresh(user)
    return user


async def create_powersync_credentials(user_id: UUID) -> tuple[str, int]:
    try:
        token, expires_in = create_powersync_token(str(user_id))
    except RuntimeError as exc:
        raise ValidationError("PowerSync signing is not configured") from exc
    return token, expires_in


def get_powersync_jwks_payload() -> dict[str, list[dict[str, object]]]:
    try:
        return get_powersync_jwks()
    except RuntimeError as exc:
        raise ValidationError("PowerSync signing is not configured") from exc


async def resend_verification_email(session: AsyncSession, email: str) -> str:
    normalized_email = _normalize_email(email)
    user = await _get_user_by_email(session, normalized_email)
    if user is None or user.primary_email_verified:
        return "If the email is registered, a verification link has been sent"

    if not email_service.is_email_delivery_configured():
        return "Email verification is not configured on this server"

    token = await _issue_email_action_token(
        session,
        user.user_id,
        EMAIL_VERIFICATION_ACTION,
        get_settings().email_verification_token_expire_minutes,
    )
    email_service.send_email(
        normalized_email,
        "Verify your Papyrus email address",
        _verification_email_body(token),
    )
    await session.commit()
    return "If the email is registered, a verification link has been sent"


async def verify_email_token(session: AsyncSession, token: str) -> str:
    email_token = await _consume_email_action_token(session, token, EMAIL_VERIFICATION_ACTION)
    user = await _get_active_user(session, email_token.user_id)
    user.primary_email_verified = True
    await session.commit()
    return "Email verified successfully"


async def begin_password_reset(session: AsyncSession, email: str) -> str:
    normalized_email = _normalize_email(email)
    user = await _get_user_by_email(session, normalized_email)
    if user is None:
        return "If the email is registered, a reset link has been sent"

    if not email_service.is_email_delivery_configured():
        return "Password reset is not configured on this server"

    token = await _issue_email_action_token(
        session,
        user.user_id,
        PASSWORD_RESET_ACTION,
        get_settings().password_reset_token_expire_minutes,
    )
    email_service.send_email(
        normalized_email,
        "Reset your Papyrus password",
        _password_reset_email_body(token),
    )
    await session.commit()
    return "If the email is registered, a reset link has been sent"


async def reset_password(session: AsyncSession, token: str, new_password: str) -> str:
    email_token = await _consume_email_action_token(session, token, PASSWORD_RESET_ACTION)
    user = await _get_active_user(session, email_token.user_id)

    credential_result = await session.execute(
        select(PasswordCredential).where(PasswordCredential.user_id == user.user_id)
    )
    credential = credential_result.scalar_one_or_none()
    if credential is None:
        credential = PasswordCredential(user_id=user.user_id, password_hash=hash_password(new_password))
        session.add(credential)
    else:
        credential.password_hash = hash_password(new_password)
        credential.password_changed_at = _now()

    await _revoke_user_sessions(session, user.user_id)
    await session.commit()
    return "Password has been reset successfully"
