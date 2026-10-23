"""Deletion capabilities cannot be used as ordinary login credentials."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.security import hash_opaque_token
from papyrus.models import AuthSession, User, UserIdentity
from papyrus.services import account_deletion, auth


async def authorize(client: AsyncClient, auth_user: dict[str, str]) -> dict:
    response = await client.post(
        "/v1/account-deletion/password",
        json={"email": "user@example.com", "password": auth_user["password"]},
    )

    assert response.status_code == 200, response.text
    return response.json()


async def test_password_schedule_recovery_requires_explicit_cancellation(
    client: AsyncClient,
    auth_user: dict[str, str],
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 10, 23, 12, tzinfo=UTC)
    monkeypatch.setattr(account_deletion, "_now", lambda: now)
    preview = await authorize(client, auth_user)
    assert preview["deletion_pending"] is False
    assert datetime.fromisoformat(preview["deletion_due_at"]) == now + timedelta(days=30)
    assert (await client.get("/v1/users/me", headers=auth_headers)).status_code == 200
    second_preview = await authorize(client, auth_user)

    scheduled = await client.post(
        "/v1/account-deletion/schedule", json={"authorization_token": preview["authorization_token"]}
    )

    assert scheduled.status_code == 200
    assert scheduled.json()["deletion_due_at"] == preview["deletion_due_at"]
    assert (await client.get("/v1/users/me", headers=auth_headers)).status_code == 401

    replay = await client.post(
        "/v1/account-deletion/schedule", json={"authorization_token": second_preview["authorization_token"]}
    )

    assert replay.status_code == 401
    login = await client.post("/v1/auth/login", json={"email": "user@example.com", "password": auth_user["password"]})
    assert login.status_code == 403
    assert login.json()["error"]["code"] == "ACCOUNT_DELETION_PENDING"
    recovery = login.json()["error"]["details"]["authorization"]
    assert recovery["deletion_pending"] is True
    assert recovery["deletion_due_at"] == preview["deletion_due_at"]
    user = await db_session.get(User, UUID(auth_user["user_id"]))
    assert user is not None
    await db_session.refresh(user)
    assert user.disabled_at is not None

    restricted = await client.get(
        "/v1/users/me", headers={"Authorization": f"Bearer {recovery['authorization_token']}"}
    )
    assert restricted.status_code == 401

    cancelled = await client.post(
        "/v1/account-deletion/cancel", json={"authorization_token": recovery["authorization_token"]}
    )

    assert cancelled.status_code == 200
    restored = cancelled.json()
    assert restored["user"]["user_id"] == auth_user["user_id"]
    assert (
        await client.get("/v1/users/me", headers={"Authorization": f"Bearer {restored['access_token']}"})
    ).status_code == 200
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": restored["refresh_token"]})).status_code == 200
    assert (
        await client.post("/v1/account-deletion/cancel", json={"authorization_token": recovery["authorization_token"]})
    ).status_code == 401
    assert (await client.get("/v1/users/me", headers=auth_headers)).status_code == 401


async def test_signed_in_deletion_uses_current_session_without_reauthentication(
    client: AsyncClient, auth_user: dict[str, str], auth_headers: dict[str, str], db_session: AsyncSession
) -> None:
    other = User(display_name="Other account")
    db_session.add(other)
    await db_session.commit()
    assert (await client.post("/v1/account-deletion/current")).status_code == 401
    before = datetime.now(UTC)
    scheduled = await client.post("/v1/account-deletion/current", headers=auth_headers)
    assert scheduled.status_code == 200
    due = datetime.fromisoformat(scheduled.json()["deletion_due_at"])
    assert before + timedelta(days=30) <= due <= datetime.now(UTC) + timedelta(days=30)
    assert (await client.get("/v1/users/me", headers=auth_headers)).status_code == 401
    assert (await client.post("/v1/account-deletion/current", headers=auth_headers)).status_code == 401
    await db_session.refresh(other)
    assert other.disabled_at is None
    assert other.deletion_due_at is None
    login = await client.post("/v1/auth/login", json={"email": "user@example.com", "password": auth_user["password"]})
    assert login.json()["error"]["code"] == "ACCOUNT_DELETION_PENDING"
    assert (
        await db_session.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None)))
        == 0
    )


async def test_password_reauthentication_and_expected_account_are_required(
    client: AsyncClient, auth_user: dict[str, str]
) -> None:
    response = await client.post(
        "/v1/account-deletion/password", json={"email": "user@example.com", "password": "incorrect"}
    )

    assert response.status_code == 401

    response = await client.post(
        "/v1/account-deletion/password",
        json={"email": "user@example.com", "password": auth_user["password"], "expected_user_id": str(uuid4())},
    )

    assert response.status_code == 403
    assert (
        await client.post("/v1/account-deletion/schedule", json={"authorization_token": "invalid"})
    ).status_code == 401


async def test_expired_preview_and_recovery_deadline_are_enforced(
    client: AsyncClient, auth_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 10, 23, 12, tzinfo=UTC)
    monkeypatch.setattr(account_deletion, "_now", lambda: now)
    preview = await authorize(client, auth_user)
    now += timedelta(minutes=5)

    response = await client.post(
        "/v1/account-deletion/schedule", json={"authorization_token": preview["authorization_token"]}
    )

    assert response.status_code == 401
    preview = await authorize(client, auth_user)

    response = await client.post(
        "/v1/account-deletion/schedule", json={"authorization_token": preview["authorization_token"]}
    )

    assert response.status_code == 200
    now += timedelta(days=30)

    response = await client.post(
        "/v1/account-deletion/password", json={"email": "user@example.com", "password": auth_user["password"]}
    )

    assert response.status_code == 403


async def test_disabled_accounts_cannot_be_recovered_without_scheduled_deletion(
    client: AsyncClient, auth_user: dict[str, str], db_session: AsyncSession
) -> None:
    user = await db_session.get(User, UUID(auth_user["user_id"]))
    assert user is not None
    user.disabled_at = datetime.now(UTC)
    await db_session.commit()

    response = await client.post(
        "/v1/account-deletion/password", json={"email": "user@example.com", "password": auth_user["password"]}
    )

    assert response.status_code == 403


async def test_google_reauthentication_is_bound_to_identity_and_browser(
    client: AsyncClient,
    auth_user: dict[str, str],
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "test-client")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "test-secret")
    verifier = "v" * 43
    challenge = hash_opaque_token(verifier)

    db_session.add(
        UserIdentity(user_id=UUID(auth_user["user_id"]), provider="google", provider_subject="linked-subject")
    )
    await db_session.commit()

    monkeypatch.setattr(
        auth.google_oauth_client,
        "exchange_code_for_identity",
        lambda *_: auth.GoogleIdentity(
            subject="linked-subject",
            email="different-provider-email@example.com",
            email_verified=True,
            display_name=None,
            avatar_url=None,
            nonce=challenge,
        ),
    )

    start = await client.get(
        "/v1/account-deletion/google/start",
        params={
            "redirect_uri": "papyrus://auth/callback",
            "challenge": challenge,
            "expected_user_id": auth_user["user_id"],
        },
    )

    parameters = parse_qs(urlsplit(start.headers["location"]).query)
    assert parameters["prompt"] == ["select_account consent"]
    assert parameters["nonce"] == [challenge]

    callback = await client.get(
        "/v1/auth/oauth/google/callback", params={"code": "provider-code", "state": parameters["state"][0]}
    )

    code = parse_qs(urlsplit(callback.headers["location"]).query)["code"][0]
    assert (await client.post("/v1/auth/exchange-code", json={"code": code})).status_code == 401

    wrong_browser = await client.post("/v1/account-deletion/exchange", json={"code": code, "verifier": "x" * 43})
    assert wrong_browser.status_code == 401
    response = await client.post("/v1/account-deletion/exchange", json={"code": code, "verifier": verifier})
    assert response.status_code == 200
    assert response.json()["user_id"] == auth_user["user_id"]
    assert (
        await client.post("/v1/account-deletion/exchange", json={"code": code, "verifier": verifier})
    ).status_code == 401
    assert (await client.get("/v1/users/me", headers=auth_headers)).json()["connected_providers"] == ["google"]
    assert await db_session.scalar(select(func.count()).select_from(AuthSession)) == 1
    authorization = response.json()["authorization_token"]
    response = await client.post("/v1/account-deletion/schedule", json={"authorization_token": authorization})
    assert response.status_code == 200
    assert (await client.get("/v1/users/me", headers=auth_headers)).status_code == 401

    login_start = await client.get("/v1/auth/oauth/google/start", params={"redirect_uri": "papyrus://auth/callback"})

    login_state = parse_qs(urlsplit(login_start.headers["location"]).query)["state"][0]

    login_callback = await client.get(
        "/v1/auth/oauth/google/callback", params={"code": "fresh-provider-code", "state": login_state}
    )

    login_code = parse_qs(urlsplit(login_callback.headers["location"]).query)["code"][0]
    login = await client.post("/v1/auth/exchange-code", json={"code": login_code})
    assert login.status_code == 403
    assert login.json()["error"]["code"] == "ACCOUNT_DELETION_PENDING"
    assert login.json()["error"]["details"]["authorization"]["deletion_pending"] is True
    assert (await client.post("/v1/auth/exchange-code", json={"code": login_code})).status_code == 401
    assert await db_session.scalar(select(func.count()).select_from(AuthSession)) == 1

    recovery_callback = await client.get(
        "/v1/auth/oauth/google/callback", params={"code": "fresh-provider-code", "state": parameters["state"][0]}
    )

    recovery_code = parse_qs(urlsplit(recovery_callback.headers["location"]).query)["code"][0]
    recovery = await client.post("/v1/account-deletion/exchange", json={"code": recovery_code, "verifier": verifier})
    assert recovery.status_code == 200
    assert recovery.json()["deletion_pending"] is True
    assert (await client.get("/v1/users/me", headers=auth_headers)).status_code == 401

    cancelled = await client.post(
        "/v1/account-deletion/cancel", json={"authorization_token": recovery.json()["authorization_token"]}
    )

    assert cancelled.status_code == 200
    restored_headers = {"Authorization": f"Bearer {cancelled.json()['access_token']}"}
    assert (await client.get("/v1/users/me", headers=restored_headers)).status_code == 200
    assert (await client.get("/v1/users/me", headers=auth_headers)).status_code == 401


@pytest.mark.parametrize("mismatch", ["identity", "nonce", "account"])
async def test_google_cannot_authorize_a_different_account_or_flow(
    client: AsyncClient,
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    monkeypatch.setattr(get_settings(), "google_oauth_client_id", "test-client")
    challenge = hash_opaque_token("v" * 43)
    db_session.add(UserIdentity(user_id=UUID(auth_user["user_id"]), provider="google", provider_subject="linked"))
    await db_session.commit()

    monkeypatch.setattr(
        auth.google_oauth_client,
        "exchange_code_for_identity",
        lambda *_: auth.GoogleIdentity(
            subject="unlinked" if mismatch == "identity" else "linked",
            email="user@example.com",
            email_verified=True,
            display_name=None,
            avatar_url=None,
            nonce="wrong" if mismatch == "nonce" else challenge,
        ),
    )

    start = await client.get(
        "/v1/account-deletion/google/start",
        params={
            "redirect_uri": "papyrus://auth/callback",
            "challenge": challenge,
            "expected_user_id": str(uuid4()) if mismatch == "account" else auth_user["user_id"],
        },
    )

    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    callback = await client.get("/v1/auth/oauth/google/callback", params={"code": "provider-code", "state": state})

    if mismatch == "nonce":
        assert callback.status_code == 400
    else:
        assert callback.status_code == 302
        assert "code" not in parse_qs(urlsplit(callback.headers["location"]).query)

    assert await db_session.scalar(select(func.count()).select_from(User)) == 1


async def test_public_resource_and_policy(client: AsyncClient) -> None:
    response = await client.get("/v1/account-deletion")
    assert response.status_code == 200
    assert "Continue with Google" in response.text
    assert "Keep account" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    policy = await client.get("/v1/account-deletion/policy")
    assert "not currently implemented" in policy.json()["retention_notice"]
