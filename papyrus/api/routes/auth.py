"""Authentication routes."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentSessionId, CurrentUserId
from papyrus.config import get_settings
from papyrus.core.database import get_db
from papyrus.schemas.auth import (
    AuthorizationUrlResponse,
    AuthTokens,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    OAuthExchangeRequest,
    OAuthLinkCompleteRequest,
    OAuthStartRequest,
    PowerSyncTokenResponse,
    RefreshTokenRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from papyrus.schemas.common import MessageResponse
from papyrus.schemas.user import User
from papyrus.services import auth as auth_service

router = APIRouter()
DBSession = Annotated[AsyncSession, Depends(get_db)]


def _auth_tokens_response(result: auth_service.AuthResult) -> AuthTokens:
    return AuthTokens(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        token_type="Bearer",
        expires_in=result.expires_in,
        user=User.model_validate(result.user),
    )


def _public_callback_url(request: Request, route_name: str) -> str:
    route_url = str(request.url_for(route_name))
    public_base_url = get_settings().public_base_url
    if public_base_url is None:
        return route_url

    route_parts = urlsplit(route_url)
    public_parts = urlsplit(public_base_url.rstrip("/"))
    return urlunsplit(
        (
            public_parts.scheme,
            public_parts.netloc,
            route_parts.path,
            route_parts.query,
            route_parts.fragment,
        )
    )


@router.post(
    "/register",
    response_model=AuthTokens,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register_user(
    request: RegisterRequest,
    http_request: Request,
    db: DBSession,
) -> AuthTokens:
    """Create a new user account with email and password."""
    result = await auth_service.register_user(db, request, http_request.headers.get("user-agent"))
    return _auth_tokens_response(result)


@router.post(
    "/login",
    response_model=AuthTokens,
    summary="Login with email and password",
)
async def login_user(
    request: LoginRequest,
    http_request: Request,
    db: DBSession,
) -> AuthTokens:
    """Authenticate a user with email and password credentials."""
    result = await auth_service.login_user(db, request, http_request.headers.get("user-agent"))
    return _auth_tokens_response(result)


@router.get(
    "/oauth/google/start",
    summary="Start Google OAuth login flow",
)
async def google_oauth_start(
    request: Request,
    redirect_uri: str = Query(..., description="App callback URI for the browser flow"),
) -> RedirectResponse:
    """Redirect the user to Google for login."""
    authorization_url = auth_service.build_google_login_authorization_url(
        redirect_uri=redirect_uri,
        callback_uri=_public_callback_url(request, "google_oauth_callback"),
    )
    return RedirectResponse(url=authorization_url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/oauth/google/callback",
    name="google_oauth_callback",
    summary="Complete the Google OAuth browser callback",
)
async def google_oauth_callback(
    request: Request,
    db: DBSession,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Handle the Google OAuth callback and redirect back to the app."""
    redirect_url = await auth_service.handle_google_callback(
        db,
        callback_uri=_public_callback_url(request, "google_oauth_callback"),
        code=code,
        state_token=state,
        error=error,
    )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


@router.post(
    "/exchange-code",
    response_model=AuthTokens,
    summary="Exchange an OAuth browser code for Papyrus tokens",
)
async def exchange_code(
    request: OAuthExchangeRequest,
    http_request: Request,
    db: DBSession,
) -> AuthTokens:
    """Exchange a one-time OAuth handoff code for Papyrus tokens."""
    result = await auth_service.exchange_login_code(db, request, http_request.headers.get("user-agent"))
    return _auth_tokens_response(result)


@router.post(
    "/refresh",
    response_model=AuthTokens,
    summary="Refresh access token",
)
async def refresh_token(
    request: RefreshTokenRequest,
    db: DBSession,
) -> AuthTokens:
    """Exchange a valid refresh token for a new access token."""
    result = await auth_service.refresh_tokens(db, request)
    return _auth_tokens_response(result)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout and invalidate the current session",
)
async def logout_user(
    user_id: CurrentUserId,
    session_id: CurrentSessionId,
    db: DBSession,
    request: LogoutRequest | None = None,
) -> Response:
    """Invalidate the current session or all sessions if requested."""
    if request is not None and request.all_devices:
        await auth_service.logout_all_sessions(db, user_id)
    else:
        await auth_service.logout_current_session(db, user_id, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout from all sessions",
)
async def logout_all(
    user_id: CurrentUserId,
    db: DBSession,
) -> Response:
    """Invalidate all refresh-token backed sessions for the user."""
    await auth_service.logout_all_sessions(db, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/link/google/start",
    response_model=AuthorizationUrlResponse,
    summary="Start Google account linking flow",
)
async def start_google_link(
    request: OAuthStartRequest,
    user_id: CurrentUserId,
    http_request: Request,
) -> AuthorizationUrlResponse:
    """Return the Google authorization URL for linking an account."""
    authorization_url = await auth_service.build_google_link_authorization_url(
        user_id=user_id,
        redirect_uri=request.redirect_uri,
        callback_uri=_public_callback_url(http_request, "google_oauth_callback"),
    )
    return AuthorizationUrlResponse(authorization_url=authorization_url)


@router.post(
    "/link/google/complete",
    response_model=User,
    summary="Complete Google account linking",
)
async def complete_google_link(
    request: OAuthLinkCompleteRequest,
    user_id: CurrentUserId,
    db: DBSession,
) -> User:
    """Consume a one-time code and link the Google identity to the authenticated user."""
    user = await auth_service.complete_google_link(db, user_id, request.code)
    return User.model_validate(user)


@router.post(
    "/powersync-token",
    response_model=PowerSyncTokenResponse,
    summary="Mint a short-lived PowerSync token",
)
async def powersync_token(user_id: CurrentUserId) -> PowerSyncTokenResponse:
    """Mint a short-lived PowerSync JWT for the authenticated user."""
    token, expires_in = await auth_service.create_powersync_credentials(user_id)
    return PowerSyncTokenResponse(token=token, expires_in=expires_in)


@router.get(
    "/jwks",
    summary="Return the PowerSync signing JWKS",
)
async def powersync_jwks() -> dict[str, list[dict[str, object]]]:
    """Expose the PowerSync public signing keys."""
    return auth_service.get_powersync_jwks_payload()


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    summary="Verify email address",
)
async def verify_email(request: VerifyEmailRequest, db: DBSession) -> MessageResponse:
    """Verify the user's email address using a one-time token."""
    message = await auth_service.verify_email_token(db, request.token)
    return MessageResponse(message=message)


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    summary="Resend verification email",
)
async def resend_verification(
    request: ResendVerificationRequest,
    db: DBSession,
) -> MessageResponse:
    """Issue a new verification token when email delivery is enabled."""
    message = await auth_service.resend_verification_email(db, request.email)
    return MessageResponse(message=message)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Request password reset",
)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: DBSession,
) -> MessageResponse:
    """Issue a password reset token when email delivery is enabled."""
    message = await auth_service.begin_password_reset(db, request.email)
    return MessageResponse(message=message)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Reset password with token",
)
async def reset_password(
    request: ResetPasswordRequest,
    db: DBSession,
) -> MessageResponse:
    """Reset the user's password using a one-time token."""
    message = await auth_service.reset_password(db, request.token, request.password)
    return MessageResponse(message=message)
