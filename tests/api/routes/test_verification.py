"""Public verification contracts, existing access guards, and generic retry responses."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select

from papyrus.config import get_settings
from papyrus.models import AuthSession, User, VerificationDelivery, VerificationRecipient


@pytest.fixture(autouse=True)
def mail_configuration(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "email_delivery_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "noreply@example.test")
    monkeypatch.setattr(settings, "app_public_base_url", "https://app.example.test")


@pytest.mark.parametrize(
    "path,method,body",
    [
        ("/v1/users/me", "get", None),
        ("/v1/auth/powersync-token", "post", None),
        ("/v1/sync/powersync-upload", "post", {"batch": []}),
        ("/v1/media/00000000-0000-4000-8000-000000000001", "get", None),
    ],
)
async def test_legacy_jwt_cannot_access_application(client, auth_user, auth_headers, db_session, path, method, body):
    user = await db_session.get(User, UUID(auth_user["user_id"]))
    user.verified_email = "old@example.com"
    await db_session.commit()
    response = await client.request(method, path, headers=auth_headers, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "EMAIL_VERIFICATION_REQUIRED"
    assert response.headers["cache-control"] == "no-store"
    assert "access_token" not in response.text


async def test_logout_remains_available_for_unverified_session(client, auth_user, auth_headers, db_session):
    user = await db_session.get(User, UUID(auth_user["user_id"]))
    user.verified_email = None
    await db_session.commit()
    assert (await client.post("/v1/auth/logout", headers=auth_headers)).status_code == 204
    await db_session.refresh(user)
    stored = await db_session.get(AuthSession, UUID(auth_user["session_id"]))
    assert stored.revoked_at is not None


@pytest.mark.parametrize("configuration", ["smtp", "landing"])
async def test_unavailable_registration_and_resend_are_retryable_and_generic(
    client, db_session, monkeypatch, configuration
):
    if configuration == "smtp":
        monkeypatch.setattr(get_settings(), "email_delivery_enabled", False)
    else:
        monkeypatch.setattr(get_settings(), "app_public_base_url", None)

    registration = await client.post(
        "/v1/auth/register", json={"email": "reader@example.com", "password": "SecureP@ss123", "display_name": "Reader"}
    )
    resend = await client.post("/v1/auth/resend-verification", json={"email": "missing@example.com"})

    for response in (registration, resend):
        assert response.status_code == 503
        assert response.headers["retry-after"] == "60"
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "error": {
                "code": "VERIFICATION_UNAVAILABLE",
                "message": "Verification email is temporarily unavailable. Please try again later.",
                "details": {},
            }
        }

    assert await db_session.scalar(select(User)) is None
    assert await db_session.scalar(select(VerificationDelivery)) is None


async def test_resend_unknown_verified_disabled_and_pending_match(client, db_session, auth_user):
    for address, disabled in [("pending@example.com", False), ("disabled@example.com", True)]:
        db_session.add(
            User(display_name="Reader", primary_email=address, disabled_at=datetime.now(UTC) if disabled else None)
        )

    await db_session.commit()
    bodies = []

    for address in [
        "missing@example.com",
        "user@example.com",
        "disabled@example.com",
        "pending@example.com",
        "PENDING@example.com",
    ]:
        response = await client.post("/v1/auth/resend-verification", json={"email": address})
        assert response.status_code == 200
        bodies.append(response.json())

    assert (
        bodies == [{"message": "If the email is registered, a verification link has been sent", "resend_after": 60}] * 5
    )
    assert await db_session.scalar(select(func.count()).select_from(VerificationDelivery)) == 1


@pytest.mark.parametrize("token", ["", "not-a-token", "x" * 2000])
async def test_invalid_verification_envelope(client, token):
    response = await client.post("/v1/auth/verify-email", json={"token": token})
    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "VERIFICATION_TOKEN_INVALID",
        "message": "This verification link is invalid or expired. Request a new link or sign in if you already verified.",
        "details": {},
    }


async def test_registration_and_resend_share_cooldown_without_resetting_it(client, db_session):
    response = await client.post(
        "/v1/auth/register", json={"email": "Reader@example.com", "password": "SecureP@ss123", "display_name": "Reader"}
    )
    assert response.status_code == 201
    before = await db_session.scalar(select(VerificationRecipient.last_queued_at))
    await client.post("/v1/auth/resend-verification", json={"email": "reader@example.com"})
    assert await db_session.scalar(select(VerificationRecipient.last_queued_at)) == before
    assert await db_session.scalar(select(func.count()).select_from(VerificationDelivery)) == 1


async def test_rate_limit_caps_configuration_and_matches_retry_header(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_auth", 100)

    for _ in range(5):
        response = await client.post("/v1/auth/login", json={"email": "missing@example.com", "password": "wrong"})
        assert response.status_code == 401

    response = await client.post("/v1/auth/login", json={"email": "missing@example.com", "password": "wrong"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) == response.json()["error"]["details"]["retry_after"]
    assert response.headers["cache-control"] == "no-store"


async def test_registration_uses_configured_api_prefix(monkeypatch, test_session_maker):
    from httpx import ASGITransport, AsyncClient

    from papyrus.core.database import get_db
    from papyrus.main import create_app

    monkeypatch.setattr(get_settings(), "api_prefix", "/custom/api")
    app = create_app()

    async def database():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = database

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/custom/api/auth/register",
            json={"email": "reader@example.com", "password": "SecureP@ss123", "display_name": "Reader"},
        )
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["status"] == "verification_required"


def test_access_logs_redact_oauth_secrets():
    import logging

    from papyrus.main import AuthQueryFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("client", "GET", "/custom/auth/oauth/google/callback?code=secret&state=secret", "1.1", 302),
        None,
    )
    assert AuthQueryFilter().filter(record)
    assert "secret" not in record.getMessage()
    assert "/custom/auth/oauth/google/callback" in record.getMessage()
