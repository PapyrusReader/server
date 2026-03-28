"""Provider-backed auth smoke tests gated by environment variables."""

from __future__ import annotations

import os

import httpx
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
):
    """Exercise a live Google-authenticated Papyrus session against the running server."""
    if not _env_flag("RUN_GOOGLE_SMOKE_TEST"):
        pytest.skip("RUN_GOOGLE_SMOKE_TEST is not enabled")

    settings = get_settings()
    base_url = os.environ.get("AUTH_SMOKE_SERVER_BASE_URL") or settings.public_base_url
    access_token = os.environ.get("AUTH_SMOKE_GOOGLE_ACCESS_TOKEN")
    refresh_token = os.environ.get("AUTH_SMOKE_GOOGLE_REFRESH_TOKEN")

    if base_url is None:
        pytest.skip("AUTH_SMOKE_SERVER_BASE_URL or PUBLIC_BASE_URL must be configured")

    if access_token is None and refresh_token is None:
        if os.environ.get("AUTH_SMOKE_GOOGLE_CODE") or os.environ.get("AUTH_SMOKE_GOOGLE_CALLBACK_URI"):
            pytest.skip(
                "Raw Google code smoke testing is no longer supported. "
                "Complete a real Google login in the auth sandbox, then set "
                "AUTH_SMOKE_GOOGLE_ACCESS_TOKEN or AUTH_SMOKE_GOOGLE_REFRESH_TOKEN."
            )

        pytest.skip("AUTH_SMOKE_GOOGLE_ACCESS_TOKEN or AUTH_SMOKE_GOOGLE_REFRESH_TOKEN is not configured")

    api_prefix = settings.api_prefix
    me_url = f"{api_prefix}/users/me"
    refresh_url = f"{api_prefix}/auth/refresh"
    current_access_token = access_token

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        if current_access_token is not None:
            me_response = await client.get(
                me_url,
                headers={"Authorization": f"Bearer {current_access_token}"},
            )

            if me_response.status_code == 200:
                body = me_response.json()
                assert "user_id" in body
                assert body["email"]
                return

            if refresh_token is None:
                pytest.fail(
                    "AUTH_SMOKE_GOOGLE_ACCESS_TOKEN was rejected and no refresh token was provided. "
                    "Log in through Google again and copy a fresh access token or refresh token from the auth sandbox."
                )

        if refresh_token is None:
            pytest.fail("No usable Google-authenticated access token or refresh token was provided")

        refresh_response = await client.post(
            refresh_url,
            json={"refresh_token": refresh_token},
        )
        assert refresh_response.status_code == 200, refresh_response.text

        refresh_body = refresh_response.json()
        assert refresh_body["access_token"]
        assert refresh_body["refresh_token"]
        assert refresh_body["refresh_token"] != refresh_token
        print(f"AUTH_SMOKE_ROTATED_REFRESH_TOKEN={refresh_body['refresh_token']}")

        current_access_token = refresh_body["access_token"]
        me_response = await client.get(
            me_url,
            headers={"Authorization": f"Bearer {current_access_token}"},
        )
        assert me_response.status_code == 200, me_response.text

        me_body = me_response.json()
        assert me_body["user_id"]
        assert me_body["email"]
