"""User service layer."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError, ValidationError
from papyrus.core.security import hash_password, verify_password
from papyrus.models import AuthSession, PasswordCredential, User
from papyrus.schemas.user import UpdateUserRequest


def _now() -> datetime:
    return datetime.now(UTC)


async def _get_active_user(session: AsyncSession, user_id: UUID) -> User:
    user = await session.get(User, user_id)

    if user is None:
        raise NotFoundError("User not found")

    if user.disabled_at is not None:
        raise ForbiddenError("User account is disabled")

    return user


async def _revoke_user_sessions(session: AsyncSession, user_id: UUID) -> None:
    result = await session.execute(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    )
    for auth_session in result.scalars():
        auth_session.revoked_at = _now()


async def get_user_profile(session: AsyncSession, user_id: UUID) -> User:
    return await _get_active_user(session, user_id)


async def update_user_profile(session: AsyncSession, user_id: UUID, request: UpdateUserRequest) -> User:
    user = await _get_active_user(session, user_id)

    if request.display_name is not None:
        user.display_name = request.display_name

    if request.avatar_url is not None:
        user.avatar_url = str(request.avatar_url)

    await session.commit()
    await session.refresh(user)
    return user


async def delete_user_account(session: AsyncSession, user_id: UUID, password: str) -> None:
    user = await _get_active_user(session, user_id)
    credential = await session.get(PasswordCredential, user_id)

    if credential is not None and not verify_password(password, credential.password_hash):
        raise UnauthorizedError("Current password is incorrect")

    user.disabled_at = _now()
    await _revoke_user_sessions(session, user_id)
    await session.commit()


async def change_user_password(session: AsyncSession, user_id: UUID, current_password: str, new_password: str) -> None:
    user = await _get_active_user(session, user_id)
    credential = await session.get(PasswordCredential, user.user_id)

    if credential is None:
        raise ValidationError("Password authentication is not enabled for this user")

    if not verify_password(current_password, credential.password_hash):
        raise UnauthorizedError("Current password is incorrect")

    credential.password_hash = hash_password(new_password)
    credential.password_changed_at = _now()
    await _revoke_user_sessions(session, user_id)
    await session.commit()
