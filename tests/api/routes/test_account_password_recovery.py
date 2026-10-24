"""Password recovery preserves deletion state until the owner explicitly cancels."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.security import generate_opaque_token, hash_opaque_token, verify_password
from papyrus.models import AuthSession, EmailActionToken, PasswordCredential, User
from papyrus.services import account_deletion, auth, email


async def test_forgot_password_during_grace_requires_explicit_cancellation(
    client: AsyncClient,
    auth_user: dict[str, str],
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []

    def capture_email(recipient: str, subject: str, body: str, **kwargs: object) -> None:
        messages.append(body)

    monkeypatch.setattr(email, "is_email_delivery_configured", lambda: True)
    monkeypatch.setattr(email, "send_email", capture_email)
    monkeypatch.setattr(get_settings(), "app_public_base_url", "https://app.example.test")
    scheduled = await client.post("/v1/account-deletion/current", headers=auth_headers)
    assert scheduled.status_code == 200
    due = scheduled.json()["deletion_due_at"]
    user_id = UUID(auth_user["user_id"])
    user = await db_session.get(User, user_id)
    assert user is not None
    requested_at = user.deletion_requested_at
    disabled_at = user.disabled_at

    forgotten = await client.post("/v1/auth/forgot-password", json={"email": "user@example.com"})

    assert forgotten.status_code == 200
    token = parse_qs(urlsplit(messages[-1].strip().splitlines()[-1]).query)["token"][0]
    password = "RecoveredP@ssword123"
    reset = await client.post("/v1/auth/reset-password", json={"token": token, "password": password})
    assert reset.status_code == 200, reset.text
    assert set(reset.json()) == {"message"}
    await db_session.refresh(user)
    assert user.disabled_at == disabled_at
    assert user.deletion_requested_at == requested_at
    assert user.deletion_due_at == datetime.fromisoformat(due)

    active_sessions = await db_session.scalar(
        select(func.count())
        .select_from(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    )

    assert active_sessions == 0
    assert (await client.get("/v1/users/me", headers=auth_headers)).status_code == 401
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": auth_user["refresh_token"]})
    ).status_code == 401
    assert (
        await client.post("/v1/auth/reset-password", json={"token": token, "password": password})
    ).status_code == 400

    login = await client.post("/v1/auth/login", json={"email": "user@example.com", "password": password})

    assert login.status_code == 403
    assert login.json()["error"]["code"] == "ACCOUNT_DELETION_PENDING"
    authorization = login.json()["error"]["details"]["authorization"]
    assert authorization["deletion_due_at"] == due

    cancelled = await client.post(
        "/v1/account-deletion/cancel", json={"authorization_token": authorization["authorization_token"]}
    )

    assert cancelled.status_code == 200
    restored_headers = {"Authorization": f"Bearer {cancelled.json()['access_token']}"}
    assert (await client.get("/v1/users/me", headers=restored_headers)).status_code == 200
    restored_login = await client.post("/v1/auth/login", json={"email": "user@example.com", "password": password})
    assert restored_login.status_code == 200

    old_login = await client.post(
        "/v1/auth/login", json={"email": "user@example.com", "password": auth_user["password"]}
    )

    assert old_login.status_code == 401


@pytest.mark.parametrize("seconds_until_deletion", [None, 0, -1])
async def test_password_reset_rejects_disabled_and_expired_recovery_accounts(
    client: AsyncClient,
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    seconds_until_deletion: int | None,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(account_deletion, "_now", lambda: now)
    user_id = UUID(auth_user["user_id"])
    user = await db_session.get(User, user_id)
    assert user is not None
    user.disabled_at = now

    if seconds_until_deletion is not None:
        user.deletion_requested_at = now - timedelta(days=30)
        user.deletion_due_at = now + timedelta(seconds=seconds_until_deletion)

    token = generate_opaque_token()

    db_session.add(
        EmailActionToken(
            user_id=user_id,
            action_type=auth.PASSWORD_RESET_ACTION,
            token_hash=hash_opaque_token(token),
            expires_at=now + timedelta(minutes=5),
        )
    )

    await db_session.commit()
    response = await client.post("/v1/auth/reset-password", json={"token": token, "password": "RejectedP@ssword123"})
    assert response.status_code == 403
    credential = await db_session.get(PasswordCredential, user_id)
    assert credential is not None
    assert verify_password(auth_user["password"], credential.password_hash)
    await db_session.refresh(user)
    assert user.disabled_at == now
