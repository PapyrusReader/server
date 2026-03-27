"""Provider-backed auth smoke tests gated by environment variables."""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.config import get_settings
from papyrus.services import auth as auth_service
from papyrus.services import email as email_service

pytestmark = [pytest.mark.integration, pytest.mark.auth_smoke]


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


async def test_smtp_password_reset_smoke(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Send a real password-reset email when SMTP smoke testing is enabled."""
    if not _env_flag("RUN_SMTP_SMOKE_TEST"):
        pytest.skip("RUN_SMTP_SMOKE_TEST is not enabled")

    recipient = os.environ.get("AUTH_SMOKE_EMAIL_RECIPIENT")
    if not recipient:
        pytest.skip("AUTH_SMOKE_EMAIL_RECIPIENT is not configured")

    if not email_service.is_email_delivery_configured():
        pytest.skip("SMTP delivery is not configured in the environment")

    async with test_session_maker() as session:
        from papyrus.models import User

        session.add(User(display_name="SMTP Smoke", primary_email=recipient, primary_email_verified=True))
        await session.commit()

        message = await auth_service.begin_password_reset(session, recipient)
        assert message == "If the email is registered, a reset link has been sent"


async def test_google_oauth_smoke(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Exercise the live Google code exchange when explicitly configured."""
    if not _env_flag("RUN_GOOGLE_SMOKE_TEST"):
        pytest.skip("RUN_GOOGLE_SMOKE_TEST is not enabled")

    google_code = os.environ.get("AUTH_SMOKE_GOOGLE_CODE")
    callback_uri = os.environ.get("AUTH_SMOKE_GOOGLE_CALLBACK_URI")
    if not google_code or not callback_uri:
        pytest.skip("AUTH_SMOKE_GOOGLE_CODE or AUTH_SMOKE_GOOGLE_CALLBACK_URI is not configured")

    settings = get_settings()
    if settings.google_oauth_client_id is None or settings.google_oauth_client_secret is None:
        pytest.skip("Google OAuth is not configured in the environment")

    async with test_session_maker() as session:
        redirect_uri = "papyrus://auth/callback"
        authorization_url = auth_service.build_google_login_authorization_url(redirect_uri, callback_uri)
        state = parse_qs(urlparse(authorization_url).query)["state"][0]

        redirect_url = await auth_service.handle_google_callback(
            session,
            callback_uri=callback_uri,
            code=google_code,
            state_token=state,
            error=None,
        )
        query = parse_qs(urlparse(redirect_url).query)
        assert "code" in query
