"""Deletion cancellation and authentication always lock the owner before credentials."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.api.deps import get_revocable_auth_session
from papyrus.core.exceptions import UnauthorizedError, ValidationError
from papyrus.core.security import hash_opaque_token
from papyrus.models import AuthSession, EmailActionToken, User
from papyrus.schemas.account_deletion import AccountPasswordRequest
from papyrus.schemas.auth import OAuthExchangeRequest
from papyrus.services import account_deletion, auth
from papyrus.services.auth import _core


async def test_authentication_reads_share_owner_lock_and_block_credential_changes(
    test_session_maker: async_sessionmaker[AsyncSession],
    auth_user: dict[str, str],
) -> None:
    payload = {"sub": auth_user["user_id"], "sid": auth_user["session_id"]}
    user_id = UUID(auth_user["user_id"])

    async with (
        test_session_maker() as first,
        test_session_maker() as second,
        test_session_maker() as transition,
        test_session_maker() as monitor,
    ):
        first_pid = await first.scalar(select(func.pg_backend_pid()))
        second_pid = await second.scalar(select(func.pg_backend_pid()))
        transition_pid = await transition.scalar(select(func.pg_backend_pid()))
        await get_revocable_auth_session(payload, first)
        session = await asyncio.wait_for(get_revocable_auth_session(payload, second), timeout=5)
        assert session.user_id == user_id
        changing = asyncio.create_task(transition.scalar(select(User).where(User.user_id == user_id).with_for_update()))

        try:
            async with asyncio.timeout(5):
                while True:
                    blockers = await monitor.scalar(select(func.pg_blocking_pids(transition_pid)))

                    if first_pid in blockers or second_pid in blockers:
                        break

                    await asyncio.sleep(0.01)

            await first.rollback()

            async with asyncio.timeout(5):
                while second_pid not in await monitor.scalar(select(func.pg_blocking_pids(transition_pid))):
                    await asyncio.sleep(0.01)

            assert not changing.done()
            await second.rollback()
            assert await asyncio.wait_for(changing, timeout=5) is not None
        finally:
            if not changing.done():
                changing.cancel()

            await asyncio.gather(changing, return_exceptions=True)


@pytest.mark.parametrize("flow", ["google", "password_reset", "verification"])
async def test_cancellation_revokes_waiting_authentication_without_deadlock(
    test_session_maker: async_sessionmaker[AsyncSession],
    auth_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    flow: str,
) -> None:
    user_id = UUID(auth_user["user_id"])

    async with test_session_maker() as setup:
        await account_deletion.schedule_current_account(setup, user_id, UUID(auth_user["session_id"]))

        recovery = await account_deletion.authenticate_password(
            setup, AccountPasswordRequest(email="user@example.com", password=auth_user["password"])
        )

        if flow == "google":
            token = await _core._create_exchange_code(setup, purpose="login", redirect_uri="", user_id=user_id)
        elif flow == "verification":
            token = await _core._issue_email_action_token(setup, user_id, auth.EMAIL_VERIFICATION_ACTION, 5)
            stored = await setup.scalar(
                select(EmailActionToken).where(EmailActionToken.token_hash == hash_opaque_token(token))
            )
            stored.email = "user@example.com"
        else:
            token = await _core._issue_email_action_token(setup, user_id, auth.PASSWORD_RESET_ACTION, 5)

        await setup.commit()

    owner_locked = asyncio.Event()
    resume_cancellation = asyncio.Event()
    revoke_credentials = account_deletion._revoke_credentials

    async def pause_before_revocation(session: AsyncSession, owner_id: UUID) -> None:
        owner_locked.set()
        await resume_cancellation.wait()
        await revoke_credentials(session, owner_id)

    monkeypatch.setattr(account_deletion, "_revoke_credentials", pause_before_revocation)

    async with (
        test_session_maker() as cancellation,
        test_session_maker() as authentication,
        test_session_maker() as monitor,
    ):
        cancellation_pid = await cancellation.scalar(select(func.pg_backend_pid()))
        authentication_pid = await authentication.scalar(select(func.pg_backend_pid()))
        cached_user = await authentication.get(User, user_id)
        assert cached_user is not None
        assert cached_user.disabled_at is not None

        async def authenticate() -> None:
            if flow == "google":
                with pytest.raises(UnauthorizedError):
                    await auth.exchange_login_code(authentication, OAuthExchangeRequest(code=token), None)
            elif flow == "verification":
                with pytest.raises(ValidationError):
                    await auth.verify_email_token(authentication, token)
            else:
                with pytest.raises(ValidationError):
                    await auth.reset_password(authentication, token, "RacingP@ssword123")

        cancellation_task = asyncio.create_task(
            account_deletion.cancel_deletion(cancellation, recovery.authorization_token)
        )

        authentication_task = None

        try:
            await asyncio.wait_for(owner_locked.wait(), timeout=5)
            authentication_task = asyncio.create_task(authenticate())

            async with asyncio.timeout(5):
                while True:
                    blockers = await monitor.scalar(select(func.pg_blocking_pids(authentication_pid)))

                    if cancellation_pid in blockers:
                        break

                    await asyncio.sleep(0.01)

            resume_cancellation.set()
            restored, _ = await asyncio.wait_for(asyncio.gather(cancellation_task, authentication_task), timeout=5)
            assert restored.user.user_id == user_id
            assert cached_user.disabled_at is None
            assert cached_user.deletion_due_at is None
        finally:
            resume_cancellation.set()
            tasks = [task for task in (cancellation_task, authentication_task) if task is not None]

            for task in tasks:
                if not task.done():
                    task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

    async with test_session_maker() as verification:
        active_sessions = await verification.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        )

        assert active_sessions == 1
