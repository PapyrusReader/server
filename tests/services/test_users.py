"""Service-layer tests for user account management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.core.exceptions import UnauthorizedError
from papyrus.core.security import hash_opaque_token, hash_password
from papyrus.models import AuthSession, PasswordCredential, User
from papyrus.services import users as user_service


async def _seed_user_with_session(
    session: AsyncSession,
    *,
    email: str = "user@example.com",
    password: str = "SecureP@ss123",
) -> tuple[User, AuthSession]:
    user = User(
        display_name="Example User",
        primary_email=email,
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()

    session.add(PasswordCredential(user_id=user.user_id, password_hash=hash_password(password)))
    auth_session = AuthSession(
        user_id=user.user_id,
        refresh_token_hash=hash_opaque_token("refresh-token"),
        client_type="web",
        device_label="pytest",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        last_seen_at=datetime.now(UTC),
    )
    session.add(auth_session)
    await session.commit()
    await session.refresh(user)
    await session.refresh(auth_session)
    return user, auth_session


async def test_change_user_password_revokes_sessions(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test password changes revoke active sessions."""
    async with test_session_maker() as session:
        user, _ = await _seed_user_with_session(session)

        await user_service.change_user_password(session, user.user_id, "SecureP@ss123", "NewSecureP@ss123")

        session_result = await session.execute(select(AuthSession).where(AuthSession.user_id == user.user_id))
        assert all(auth_session.revoked_at is not None for auth_session in session_result.scalars())


async def test_change_user_password_rejects_invalid_current_password(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test password changes reject an invalid current password."""
    async with test_session_maker() as session:
        user, _ = await _seed_user_with_session(session)

        with pytest.raises(UnauthorizedError):
            await user_service.change_user_password(session, user.user_id, "WrongPassword123", "NewSecureP@ss123")


async def test_delete_user_account_disables_user_and_revokes_sessions(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Test account deletion disables the user and revokes sessions."""
    async with test_session_maker() as session:
        user, _ = await _seed_user_with_session(session)

        await user_service.delete_user_account(session, user.user_id, "SecureP@ss123")

        await session.refresh(user)
        assert user.disabled_at is not None

        session_result = await session.execute(select(AuthSession).where(AuthSession.user_id == user.user_id))
        assert all(auth_session.revoked_at is not None for auth_session in session_result.scalars())
