"""Development-only authentication sandbox routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from papyrus.api.deps import get_current_access_token_payload, get_current_auth_session
from papyrus.config import get_settings
from papyrus.core.dev_pages import render_dev_page
from papyrus.models import AuthSession

router = APIRouter(tags=["Dev"])
CurrentAuthSession = Annotated[AuthSession, Depends(get_current_auth_session)]
CurrentAccessPayload = Annotated[dict[str, Any], Depends(get_current_access_token_payload)]


def _build_auth_sandbox_config(request: Request) -> dict[str, str]:
    settings = get_settings()
    api_prefix = settings.api_prefix

    return {
        "registerUrl": f"{api_prefix}/auth/register",
        "loginUrl": f"{api_prefix}/auth/login",
        "refreshUrl": f"{api_prefix}/auth/refresh",
        "logoutUrl": f"{api_prefix}/auth/logout",
        "logoutAllUrl": f"{api_prefix}/auth/logout-all",
        "googleStartUrl": f"{api_prefix}/auth/oauth/google/start",
        "googleLinkStartUrl": f"{api_prefix}/auth/link/google/start",
        "googleLinkCompleteUrl": f"{api_prefix}/auth/link/google/complete",
        "exchangeUrl": f"{api_prefix}/auth/exchange-code",
        "forgotPasswordUrl": f"{api_prefix}/auth/forgot-password",
        "resendVerificationUrl": f"{api_prefix}/auth/resend-verification",
        "verifyEmailUrl": f"{api_prefix}/auth/verify-email",
        "resetPasswordUrl": f"{api_prefix}/auth/reset-password",
        "powersyncUrl": f"{api_prefix}/auth/powersync-token",
        "meUrl": f"{api_prefix}/users/me",
        "sessionUrl": str(request.url_for("auth_sandbox_session")),
        "redirectUri": str(request.url.replace(query="")),
    }


@router.get("/__dev/auth-sandbox", response_class=HTMLResponse)
async def auth_sandbox(request: Request) -> HTMLResponse:
    """Render the development-only authentication sandbox."""
    return render_dev_page(
        request,
        template_name="auth_sandbox.html",
        page_title="Papyrus Authentication sandbox",
        page_id="auth-sandbox",
        body_class="dev-page--auth",
        entry_module="src/pages/auth-sandbox/main.ts",
        page_config=_build_auth_sandbox_config(request),
    )


@router.get("/__dev/auth-sandbox/session", name="auth_sandbox_session")
async def auth_sandbox_session(
    auth_session: CurrentAuthSession,
    payload: CurrentAccessPayload,
) -> dict[str, Any]:
    """Return decoded token and backing DB session state for the sandbox page."""
    return {
        "access_payload": payload,
        "session": {
            "session_id": str(auth_session.session_id),
            "user_id": str(auth_session.user_id),
            "client_type": auth_session.client_type,
            "device_label": auth_session.device_label,
            "created_at": auth_session.created_at.isoformat(),
            "expires_at": auth_session.expires_at.isoformat(),
            "revoked_at": auth_session.revoked_at.isoformat() if auth_session.revoked_at is not None else None,
            "last_seen_at": auth_session.last_seen_at.isoformat() if auth_session.last_seen_at is not None else None,
            "user_disabled_at": (
                auth_session.user.disabled_at.isoformat() if auth_session.user.disabled_at is not None else None
            ),
        },
    }
