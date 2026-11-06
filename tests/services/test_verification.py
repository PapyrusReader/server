"""Verification evidence, delivery, and concurrency regressions against PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from papyrus.config import get_settings
from papyrus.core.exceptions import (
    AppError,
    VerificationRequiredError,
    VerificationTokenInvalidError,
    VerificationUnavailableError,
)
from papyrus.core.security import create_state_token, hash_opaque_token
from papyrus.models import (
    AuthExchangeCode,
    AuthSession,
    EmailActionToken,
    User,
    UserIdentity,
    VerificationDelivery,
    VerificationRecipient,
)
from papyrus.schemas.auth import LoginRequest, OAuthExchangeRequest, RefreshTokenRequest, RegisterRequest
from papyrus.services import account_deletion, auth, email
from papyrus.services.auth import verification


@pytest.fixture(autouse=True)
def mail_configuration(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "email_delivery_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "noreply@example.test")
    monkeypatch.setattr(settings, "app_public_base_url", "https://app.example.test/reader")


async def register(session, address="reader@example.com"):
    await auth.register_user(
        session, RegisterRequest(email=address, password="SecureP@ss123", display_name="Reader"), None
    )
    return await session.scalar(select(User).where(User.primary_email == address))


async def test_pending_denial_and_explicit_verification(test_session_maker):
    async with test_session_maker() as session:
        user = await register(session)
        assert await session.scalar(select(AuthSession)) is None

        with pytest.raises(VerificationRequiredError) as denied:
            await auth.login_user(session, LoginRequest(email=user.primary_email, password="SecureP@ss123"), None)

        assert denied.value.details == {"email": user.primary_email, "resend_after": 60}
        job = await session.scalar(select(VerificationDelivery))
        token = await session.get(EmailActionToken, job.token_id)
        assert token.email == user.primary_email
        assert token.token_hash == hash_opaque_token(job.token)
        assert 86395 <= (token.expires_at - datetime.now(UTC)).total_seconds() <= 86400
        await auth.verify_email_token(session, job.token)
        assert await session.scalar(select(AuthSession)) is None
        result = await auth.login_user(session, LoginRequest(email=user.primary_email, password="SecureP@ss123"), None)
        assert result.user.email_verified
        assert result.user.email_verification_source == "papyrus_email"
        assert await session.scalar(select(VerificationDelivery)) is None


@pytest.mark.parametrize("state", ["expired", "used", "wrong_address", "disabled", "past_deletion", "unbound"])
async def test_invalid_verification_is_uniform(test_session_maker, state):
    async with test_session_maker() as session:
        user = await register(session)
        job = await session.scalar(select(VerificationDelivery))
        token = await session.get(EmailActionToken, job.token_id)
        plain = job.token
        user_id = user.user_id

        if state == "expired":
            token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif state == "used":
            token.used_at = datetime.now(UTC)
        elif state == "wrong_address":
            user.primary_email = "changed@example.com"
        elif state == "disabled":
            user.disabled_at = datetime.now(UTC)
        elif state == "past_deletion":
            user.deletion_due_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            token.email = None

        await session.commit()

        with pytest.raises(VerificationTokenInvalidError):
            await auth.verify_email_token(session, plain)

    async with test_session_maker() as session:
        assert await session.scalar(select(AuthSession)) is None
        assert not (await session.get(User, user_id)).email_verified


async def test_resend_keeps_links_and_consumes_all_on_success(test_session_maker):
    async with test_session_maker() as session:
        user = await register(session)
        first = await session.scalar(select(VerificationDelivery))
        first_token = first.token
        recipient = await session.get(VerificationRecipient, user.primary_email)
        recipient.last_queued_at -= timedelta(seconds=61)
        await session.commit()
        await auth.resend_verification_email(session, user.primary_email)
        assert await session.scalar(select(func.count()).select_from(VerificationDelivery)) == 2
        await auth.verify_email_token(session, first_token)
        assert await session.scalar(select(VerificationDelivery)) is None
        assert await session.scalar(select(EmailActionToken).where(EmailActionToken.used_at.is_(None))) is None

        with pytest.raises(VerificationTokenInvalidError):
            await auth.verify_email_token(session, first_token)


async def test_cooldown_and_consumption_serialize_across_connections(test_session_maker):
    async with test_session_maker() as session:
        user = await register(session)
        await session.execute(
            update(VerificationRecipient).values(last_queued_at=datetime.now(UTC) - timedelta(seconds=61))
        )
        await session.commit()

    async def resend():
        async with test_session_maker() as session:
            await auth.resend_verification_email(session, user.primary_email.upper())

    await asyncio.gather(resend(), resend(), resend())

    async with test_session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(VerificationDelivery)) == 2
        tokens = list((await session.scalars(select(VerificationDelivery.token))).all())

    async def verify(token):
        async with test_session_maker() as session:
            try:
                await auth.verify_email_token(session, token)
                return True
            except VerificationTokenInvalidError:
                return False

    assert sorted(await asyncio.gather(*(verify(token) for token in tokens))) == [False, True]


async def test_delivery_failure_retries_same_secret_without_extending_expiry(test_session_maker, monkeypatch, caplog):
    sent = []

    def fail(*args, **kwargs):
        raise RuntimeError("SMTP secret must not be logged")

    monkeypatch.setattr(email, "send_email", fail)

    async with test_session_maker() as session:
        await register(session)
        job = await session.scalar(select(VerificationDelivery))
        action = await session.get(EmailActionToken, job.token_id)
        original_token, original_expiry = job.token, action.expires_at
        assert await verification.deliver_pending_verifications(session) == 0
        await session.refresh(job)
        await session.refresh(action)
        assert job.attempts == 1
        assert job.next_attempt_at > datetime.now(UTC)
        assert job.token == original_token and action.expires_at == original_expiry
        assert original_token not in caplog.text
        assert "SMTP secret" not in caplog.text
        job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        monkeypatch.setattr(email, "send_email", lambda *args: sent.append(args))
        assert await verification.deliver_pending_verifications(session) == 1
        assert f"https://app.example.test/reader/verify-email?token={original_token}" in sent[0][2]
        assert await session.scalar(select(VerificationDelivery)) is None
        token = await session.scalar(select(EmailActionToken))
        assert token.expires_at == original_expiry


async def test_expired_delivery_removed_without_smtp(test_session_maker, monkeypatch):
    monkeypatch.setattr(email, "send_email", lambda *args: pytest.fail("expired mail sent"))

    async with test_session_maker() as session:
        await register(session)
        await session.execute(update(EmailActionToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await session.commit()
        assert await verification.deliver_pending_verifications(session) == 0
        assert await session.scalar(select(VerificationDelivery)) is None


async def test_queue_failure_rolls_back_registration(test_session_maker, monkeypatch):
    from papyrus.services.auth import sessions

    async def fail(session, user):
        await verification.queue_verification(session, user)
        raise SQLAlchemyError("queue transaction failed")

    monkeypatch.setattr(sessions, "queue_verification", fail)

    async with test_session_maker() as session:
        with pytest.raises(VerificationUnavailableError):
            await register(session)

        assert await session.scalar(select(User)) is None
        assert await session.scalar(select(EmailActionToken)) is None
        assert await session.scalar(select(VerificationDelivery)) is None
        assert await session.scalar(select(VerificationRecipient)) is None


@pytest.mark.parametrize(
    "address,verified,hd,eligible",
    [
        ("reader@gmail.com", False, None, True),
        ("reader@workspace.example", True, "workspace.example", True),
        ("reader@workspace.example", False, "workspace.example", False),
        ("reader@workspace.example", True, None, False),
        ("reader@other.example", True, "", False),
    ],
)
async def test_google_authority_and_pending_handoff(test_session_maker, monkeypatch, address, verified, hd, eligible):
    identity = auth.GoogleIdentity("subject", address, verified, "Reader", None, hosted_domain=hd)
    monkeypatch.setattr(auth.google_oauth_client, "exchange_code_for_identity", lambda *args: identity)

    async with test_session_maker() as session:
        redirect = await auth.handle_google_callback(
            session,
            callback_uri="https://api.example.test/v1/auth/oauth/google/callback",
            code="provider",
            state_token=create_state_token({"mode": "login", "redirect_uri": "papyrus://auth/callback"}),
            error=None,
        )

        code = parse_qs(urlsplit(redirect).query)["code"][0]
        assert await session.scalar(select(AuthSession)) is None
        stored = await session.scalar(select(UserIdentity))
        assert stored.hosted_domain == hd

        if eligible:
            result = await auth.exchange_login_code(session, OAuthExchangeRequest(code=code), None)
            assert result.user.email_verified
            assert stored.provider_subject == "subject"
        else:
            job = await session.scalar(select(VerificationDelivery))
            await auth.verify_email_token(session, job.token)

            with pytest.raises(VerificationRequiredError):
                await auth.exchange_login_code(session, OAuthExchangeRequest(code=code), None)

            assert await session.scalar(select(AuthSession)) is None

        with pytest.raises(AppError) as reused:
            await auth.exchange_login_code(session, OAuthExchangeRequest(code=code), None)

        assert reused.value.status_code == 401


@pytest.mark.parametrize(
    "address,expected",
    [(None, "email_required"), ("reader@other.example", "verification_unavailable"), ("reader@gmail.com", None)],
)
async def test_google_missing_email_and_delivery_outage(test_session_maker, monkeypatch, address, expected):
    monkeypatch.setattr(get_settings(), "email_delivery_enabled", False)
    identity = auth.GoogleIdentity("subject", address, True, "Reader", None)
    monkeypatch.setattr(auth.google_oauth_client, "exchange_code_for_identity", lambda *args: identity)

    async with test_session_maker() as session:
        redirect = await auth.handle_google_callback(
            session,
            callback_uri="https://api.example.test/v1/auth/oauth/google/callback",
            code="provider",
            state_token=create_state_token({"mode": "login", "redirect_uri": "papyrus://auth/callback"}),
            error=None,
        )

        query = parse_qs(urlsplit(redirect).query)

        if expected:
            assert query == {"error": [expected]}
            assert await session.scalar(select(User)) is None
            assert await session.scalar(select(AuthExchangeCode)) is None
        else:
            assert "code" in query


async def test_legacy_refresh_revocation_commits_and_rotation_serializes(test_session_maker, auth_user):
    async with test_session_maker() as session:
        user = await session.scalar(select(User))
        user.email_verified_at = None
        user.email_verification_source = None
        await session.commit()

        with pytest.raises(VerificationRequiredError):
            await auth.refresh_tokens(session, RefreshTokenRequest(refresh_token=auth_user["refresh_token"]))

    async with test_session_maker() as session:
        stored = await session.scalar(select(AuthSession))
        assert stored.revoked_at is not None

        with pytest.raises(AppError) as retry:
            await auth.refresh_tokens(session, RefreshTokenRequest(refresh_token=auth_user["refresh_token"]))

        assert retry.value.status_code == 401


async def test_concurrent_refresh_only_one_succeeds(test_session_maker, auth_user):
    async def refresh():
        async with test_session_maker() as session:
            try:
                await auth.refresh_tokens(session, RefreshTokenRequest(refresh_token=auth_user["refresh_token"]))
                return True
            except AppError:
                return False

    assert sorted(await asyncio.gather(refresh(), refresh())) == [False, True]


async def test_recovery_requires_evidence_without_consuming_authorization(test_session_maker, auth_user):
    from papyrus.schemas.account_deletion import AccountPasswordRequest

    async with test_session_maker() as session:
        proof = await account_deletion.authenticate_password(
            session, AccountPasswordRequest(email="user@example.com", password=auth_user["password"])
        )
        await account_deletion.schedule_deletion(session, proof.authorization_token)
        user = await session.scalar(select(User))
        user.email_verified_at = None
        user.email_verification_source = None
        await session.commit()
        proof = await account_deletion.authenticate_password(
            session, AccountPasswordRequest(email="user@example.com", password=auth_user["password"])
        )

        with pytest.raises(VerificationRequiredError):
            await account_deletion.cancel_deletion(session, proof.authorization_token)

        authorization = await session.scalar(
            select(AuthExchangeCode).where(AuthExchangeCode.code_hash == hash_opaque_token(proof.authorization_token))
        )
        assert authorization.used_at is None
        assert user.deletion_due_at is not None
        await auth.resend_verification_email(session, user.primary_email)
        job = await session.scalar(select(VerificationDelivery))
        await auth.verify_email_token(session, job.token)
        assert user.deletion_due_at is not None and user.disabled_at is not None
        result = await account_deletion.cancel_deletion(session, proof.authorization_token)
        assert result.user.email_verified


@pytest.mark.parametrize(
    "primary,provider_email,expected",
    [
        ("original@example.com", "changed@gmail.com", "pending"),
        (None, None, "email_required"),
        (None, "occupied@gmail.com", "account_exists"),
        (None, "available@gmail.com", "verified"),
    ],
)
async def test_linked_google_preserves_owner_and_primary_email(
    test_session_maker, monkeypatch, primary, provider_email, expected
):
    identity = auth.GoogleIdentity("linked", provider_email, True, "Provider", None)
    monkeypatch.setattr(auth.google_oauth_client, "exchange_code_for_identity", lambda *args: identity)

    async with test_session_maker() as session:
        user = User(display_name="Original", primary_email=primary)
        session.add(user)
        session.add(User(display_name="Other", primary_email="occupied@gmail.com"))
        await session.flush()
        user_id = user.user_id
        session.add(UserIdentity(user_id=user_id, provider="google", provider_subject="linked"))
        await session.commit()

        redirect = await auth.handle_google_callback(
            session,
            callback_uri="https://api.example.test/v1/auth/oauth/google/callback",
            code="provider",
            state_token=create_state_token({"mode": "login", "redirect_uri": "papyrus://auth/callback"}),
            error=None,
        )

        query = parse_qs(urlsplit(redirect).query)
        stored = await session.get(User, user_id)
        assert await session.scalar(select(func.count()).select_from(User)) == 2
        assert stored.display_name == "Original"

        if expected in {"account_exists", "email_required"}:
            assert query == {"error": [expected]}
            assert stored.primary_email == primary
            assert await session.scalar(select(AuthExchangeCode)) is None
        elif expected == "pending":
            job = await session.scalar(select(VerificationDelivery))
            action = await session.get(EmailActionToken, job.token_id)
            assert action.email == primary
            assert stored.primary_email == primary
            assert not stored.email_verified
        else:
            assert stored.primary_email == provider_email
            assert stored.email_verified


async def test_delivery_lease_prevents_two_workers_sending_the_same_job(test_session_maker, monkeypatch):
    sent = []
    monkeypatch.setattr(email, "send_email", lambda *args: sent.append(args))

    async with test_session_maker() as session:
        await register(session)

    async def deliver():
        async with test_session_maker() as session:
            return await verification.deliver_pending_verifications(session)

    assert sum(await asyncio.gather(deliver(), deliver())) == 1
    assert len(sent) == 1


async def test_duplicate_registration_race_preserves_original_account(test_session_maker):
    async def create():
        async with test_session_maker() as session:
            try:
                await register(session)
                return 201
            except AppError as error:
                return error.status_code

    assert sorted(await asyncio.gather(create(), create())) == [201, 409]

    async with test_session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(VerificationDelivery)) == 1
        assert await session.scalar(select(AuthSession)) is None


async def test_new_account_cannot_commit_without_its_delivery_during_recipient_cooldown(test_session_maker):
    async with test_session_maker() as session:
        session.add(VerificationRecipient(email="reader@example.com", last_queued_at=datetime.now(UTC)))
        await session.commit()

        with pytest.raises(VerificationUnavailableError):
            await register(session)

        assert await session.scalar(select(User)) is None
        assert await session.scalar(select(EmailActionToken)) is None
        assert await session.scalar(select(VerificationDelivery)) is None


async def test_email_change_clears_evidence_and_revokes_credentials_without_touching_library(
    test_session_maker, auth_user
):
    from uuid import UUID

    from papyrus.models import SyncBook
    from papyrus.services.auth import _core
    from papyrus.services.auth.email_flows import change_primary_email

    user_id = UUID(auth_user["user_id"])

    async with test_session_maker() as session:
        session.add(SyncBook(owner_user_id=user_id, title="Durable library", current_cfi="epub:0:42"))
        reset_token = await _core._issue_email_action_token(session, user_id, auth.PASSWORD_RESET_ACTION, 60)
        handoff = await _core._create_exchange_code(session, user_id=user_id, purpose="login", redirect_uri="")
        await session.commit()
        user = await change_primary_email(session, user_id, "Replacement@Example.com")
        await session.commit()
        assert user.primary_email == "replacement@example.com"
        assert user.email_verified_at is None
        assert user.email_verification_source is None
        assert not user.email_verified
        assert await session.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None))) is None
        assert await session.scalar(select(EmailActionToken).where(EmailActionToken.used_at.is_(None))) is None
        assert await session.scalar(select(AuthExchangeCode).where(AuthExchangeCode.used_at.is_(None))) is None
        assert (
            await session.scalar(select(SyncBook.current_cfi).where(SyncBook.owner_user_id == user_id)) == "epub:0:42"
        )

        with pytest.raises(AppError):
            await auth.reset_password(session, reset_token, "NewSecureP@ss123")

        with pytest.raises(AppError):
            await auth.exchange_login_code(session, OAuthExchangeRequest(code=handoff), None)


async def test_email_change_away_and_back_cannot_reactivate_old_verification(test_session_maker):
    from papyrus.services.auth.email_flows import change_primary_email

    async with test_session_maker() as session:
        user = await register(session)
        user_id = user.user_id
        token = await session.scalar(select(VerificationDelivery.token))
        await change_primary_email(session, user_id, "replacement@example.com")
        await session.commit()
        assert await session.scalar(select(VerificationDelivery)) is None
        await change_primary_email(session, user_id, "reader@example.com")
        await session.commit()

        with pytest.raises(VerificationTokenInvalidError):
            await auth.verify_email_token(session, token)

        assert not user.email_verified


async def test_email_change_noop_and_rollback_preserve_verified_access(test_session_maker, auth_user):
    from uuid import UUID

    from papyrus.services.auth.email_flows import change_primary_email

    user_id = UUID(auth_user["user_id"])

    async with test_session_maker() as session:
        user = await change_primary_email(session, user_id, " USER@EXAMPLE.COM ")
        await session.commit()
        assert user.email_verified
        assert await session.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None))) is not None
        await change_primary_email(session, user_id, "replacement@example.com")
        await session.rollback()

    async with test_session_maker() as session:
        user = await session.get(User, user_id)
        assert user.primary_email == "user@example.com"
        assert user.email_verified
        assert await session.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None))) is not None


async def test_email_change_serializes_with_waiting_verification(test_session_maker, monkeypatch):
    from papyrus.services.auth import email_flows

    async with test_session_maker() as session:
        user = await register(session)
        user_id = user.user_id
        token = await session.scalar(select(VerificationDelivery.token))

    locked = asyncio.Event()
    resume = asyncio.Event()
    revoke = email_flows._revoke_credentials

    async def pause_before_revocation(session, owner_id):
        locked.set()
        await resume.wait()
        await revoke(session, owner_id)

    monkeypatch.setattr(email_flows, "_revoke_credentials", pause_before_revocation)

    async with test_session_maker() as changing, test_session_maker() as confirming, test_session_maker() as monitor:
        changing_pid = await changing.scalar(select(func.pg_backend_pid()))
        confirming_pid = await confirming.scalar(select(func.pg_backend_pid()))

        async def change():
            await email_flows.change_primary_email(changing, user_id, "replacement@example.com")
            await changing.commit()

        async def confirm():
            with pytest.raises(VerificationTokenInvalidError):
                await auth.verify_email_token(confirming, token)

        change_task = asyncio.create_task(change())
        confirm_task = None

        try:
            await asyncio.wait_for(locked.wait(), timeout=5)
            confirm_task = asyncio.create_task(confirm())

            async with asyncio.timeout(5):
                while changing_pid not in await monitor.scalar(select(func.pg_blocking_pids(confirming_pid))):
                    await asyncio.sleep(0.01)

            resume.set()
            await asyncio.wait_for(asyncio.gather(change_task, confirm_task), timeout=5)
        finally:
            resume.set()
            tasks = [task for task in (change_task, confirm_task) if task is not None]

            for task in tasks:
                if not task.done():
                    task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

    async with test_session_maker() as session:
        user = await session.get(User, user_id)
        assert user.primary_email == "replacement@example.com"
        assert not user.email_verified
        assert await session.scalar(select(VerificationDelivery)) is None


@pytest.mark.parametrize(
    "address,timestamp,source",
    [
        (None, True, "papyrus_email"),
        ("reader@example.com", True, None),
        ("reader@example.com", False, "papyrus_email"),
        ("reader@example.com", True, "unknown"),
    ],
)
async def test_database_rejects_inconsistent_verification_evidence(test_session_maker, address, timestamp, source):
    from sqlalchemy.exc import IntegrityError

    async with test_session_maker() as session:
        session.add(
            User(
                display_name="Reader",
                primary_email=address,
                email_verified_at=datetime.now(UTC) if timestamp else None,
                email_verification_source=source,
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()
