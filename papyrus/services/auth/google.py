"""Google OAuth service functions."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from papyrus.config import get_settings
from papyrus.core.exceptions import ConflictError, ForbiddenError, ValidationError
from papyrus.core.security import create_state_token, decode_state_token
from papyrus.models import User, UserIdentity
from papyrus.services.auth._core import (
    _build_redirect_uri,
    _create_exchange_code,
    _default_display_name,
    _get_active_user,
    _get_user_by_email,
    _normalize_email,
    _now,
)
from papyrus.services.auth.types import GOOGLE_PROVIDER, GoogleIdentity

GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


class GoogleOAuthClient:
    """Google OAuth helper client."""

    def __init__(self) -> None:
        self._jwk_client = jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True)

    def build_authorization_url(self, callback_uri: str, state: str) -> str:
        settings = get_settings()

        if settings.google_oauth_client_id is None:
            raise ValidationError("Google OAuth is not configured")

        query = urlencode({
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": callback_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        })

        return f"{GOOGLE_AUTHORIZATION_URL}?{query}"

    def exchange_code_for_identity(self, code: str, callback_uri: str) -> GoogleIdentity:
        settings = get_settings()

        if settings.google_oauth_client_id is None or settings.google_oauth_client_secret is None:
            raise ValidationError("Google OAuth is not configured")

        payload = urlencode({
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": callback_uri,
            "grant_type": "authorization_code",
        }).encode("utf-8")

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
            email=_normalize_email(email) if isinstance(email, str) else None,
            email_verified=bool(claims.get("email_verified", False)),
            display_name=str(claims["name"]) if isinstance(claims.get("name"), str) else None,
            avatar_url=str(claims["picture"]) if isinstance(claims.get("picture"), str) else None,
        )


google_oauth_client = GoogleOAuthClient()


def _build_google_state(redirect_uri: str, mode: str, user_id: UUID | None = None) -> str:
    payload: dict[str, str] = {"redirect_uri": redirect_uri, "mode": mode}

    if user_id is not None:
        payload["user_id"] = str(user_id)

    return create_state_token(payload)


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


async def complete_google_link(session: AsyncSession, user_id: UUID, code: str) -> User:
    from papyrus.services.auth._core import _consume_exchange_code
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
