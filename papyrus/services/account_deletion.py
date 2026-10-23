"""Reauthentication capabilities, explicit recovery, and retryable account erasure."""

import asyncio
import logging
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.config import get_settings
from papyrus.core.exceptions import AppError, ForbiddenError, UnauthorizedError, ValidationError
from papyrus.core.security import generate_opaque_token, hash_opaque_token, verify_password
from papyrus.models import (
    AccountMediaCleanup,
    AuthExchangeCode,
    AuthSession,
    EmailActionToken,
    PasswordCredential,
    User,
)
from papyrus.schemas.account_deletion import AccountDeletionAuthorization, AccountDeletionStatus, AccountPasswordRequest
from papyrus.services.account_notifications import deliver_pending_notifications, queue_account_notification
from papyrus.services.auth._core import _create_session_for_user
from papyrus.services.auth.types import AuthResult
from papyrus.services.media import media_root

RECOVERY_WINDOW = timedelta(days=30)
AUTHORIZATION_LIFETIME = timedelta(minutes=5)
AUTHORIZATION_PURPOSE = "account_deletion"
GOOGLE_HANDOFF_PURPOSE = "account_deletion_google"
logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


async def _lock_user(session: AsyncSession, user_id: UUID) -> User:
    user = await session.scalar(
        select(User).where(User.user_id == user_id).with_for_update().execution_options(populate_existing=True)
    )

    if user is None:
        raise UnauthorizedError("Account is unavailable")

    if user.deletion_due_at is not None and user.deletion_due_at <= _now():
        raise ForbiddenError("The recovery deadline has passed")

    if user.disabled_at is not None and user.deletion_due_at is None:
        raise ForbiddenError("User account is disabled")

    return user


async def _authorize(session: AsyncSession, user: User) -> AccountDeletionAuthorization:
    now = _now()
    token = generate_opaque_token()
    expires_at = now + AUTHORIZATION_LIFETIME

    session.add(
        AuthExchangeCode(
            code_hash=hash_opaque_token(token),
            purpose=AUTHORIZATION_PURPOSE,
            user_id=user.user_id,
            redirect_uri="",
            created_at=now,
            expires_at=expires_at,
        )
    )

    await session.commit()

    return AccountDeletionAuthorization(
        authorization_token=token,
        user_id=user.user_id,
        email=user.primary_email,
        deletion_pending=user.deletion_due_at is not None,
        deletion_due_at=user.deletion_due_at or now + RECOVERY_WINDOW,
        expires_at=expires_at,
    )


async def require_account_recovery(session: AsyncSession, user_id: UUID) -> NoReturn:
    """Offer recovery after successful sign-in without granting a session."""
    user = await _lock_user(session, user_id)

    if user.deletion_due_at is None:
        raise UnauthorizedError("Account status changed. Sign in again.")

    authorization = await _authorize(session, user)

    raise AppError(
        "This account is scheduled for deletion.",
        code="ACCOUNT_DELETION_PENDING",
        status_code=403,
        details={"authorization": authorization.model_dump(mode="json")},
    )


async def authenticate_password(
    session: AsyncSession,
    request: AccountPasswordRequest,
) -> AccountDeletionAuthorization:
    result = await session.execute(
        select(User.user_id, PasswordCredential.password_hash)
        .join(PasswordCredential, PasswordCredential.user_id == User.user_id)
        .where(User.primary_email == request.email.strip().lower())
    )

    credentials = result.one_or_none()

    if credentials is None or not verify_password(request.password, credentials.password_hash):
        raise UnauthorizedError("Invalid email or password")

    if request.expected_user_id is not None and credentials.user_id != request.expected_user_id:
        raise ForbiddenError("Authenticate with the account currently signed in")

    user = await _lock_user(session, credentials.user_id)
    return await _authorize(session, user)


async def _lock_authorization(session: AsyncSession, token: str, purpose: str) -> tuple[User, AuthExchangeCode]:
    """Lock the owner before the capability, matching scheduling and purge lock order."""
    token_hash = hash_opaque_token(token)

    user_id = await session.scalar(
        select(AuthExchangeCode.user_id).where(
            AuthExchangeCode.code_hash == token_hash, AuthExchangeCode.purpose == purpose
        )
    )

    if user_id is None:
        raise UnauthorizedError("Authorization is invalid or expired. Authenticate again.")

    user = await _lock_user(session, user_id)

    authorization = await session.scalar(
        select(AuthExchangeCode)
        .where(AuthExchangeCode.code_hash == token_hash, AuthExchangeCode.purpose == purpose)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if authorization is None or authorization.used_at is not None or authorization.expires_at <= _now():
        raise UnauthorizedError("Authorization is invalid or expired. Authenticate again.")

    return user, authorization


async def exchange_google(session: AsyncSession, code: str, verifier: str) -> AccountDeletionAuthorization:
    user, handoff = await _lock_authorization(session, code, GOOGLE_HANDOFF_PURPOSE)

    if handoff.code_challenge is None or not secrets.compare_digest(
        handoff.code_challenge, hash_opaque_token(verifier)
    ):
        raise UnauthorizedError("Google authorization does not match this browser session")

    handoff.used_at = _now()
    return await _authorize(session, user)


async def _revoke_credentials(session: AsyncSession, user_id: UUID) -> None:
    now = _now()
    await session.execute(update(AuthSession).where(AuthSession.user_id == user_id).values(revoked_at=now))
    await session.execute(update(AuthExchangeCode).where(AuthExchangeCode.user_id == user_id).values(used_at=now))
    await session.execute(update(EmailActionToken).where(EmailActionToken.user_id == user_id).values(used_at=now))


async def schedule_deletion(session: AsyncSession, token: str) -> AccountDeletionStatus:
    user, authorization = await _lock_authorization(session, token, AUTHORIZATION_PURPOSE)
    return await _schedule_deletion(session, user, authorization.created_at)


async def schedule_current_account(session: AsyncSession, user_id: UUID, session_id: UUID) -> AccountDeletionStatus:
    """Serialize deletion with recovery and verify that the initiating session remains active."""
    user = await _lock_user(session, user_id)

    active_session = await session.scalar(
        select(AuthSession)
        .where(
            AuthSession.session_id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > _now(),
        )
        .execution_options(populate_existing=True)
    )

    if active_session is None:
        raise UnauthorizedError("Session is no longer active")

    return await _schedule_deletion(session, user, _now())


async def _schedule_deletion(session: AsyncSession, user: User, requested_at: datetime) -> AccountDeletionStatus:
    if user.deletion_due_at is not None:
        raise ValidationError("Deletion is already scheduled")

    user.deletion_requested_at = requested_at
    user.deletion_due_at = requested_at + RECOVERY_WINDOW
    user.disabled_at = _now()
    await _revoke_credentials(session, user.user_id)
    await queue_account_notification(session, user)
    await session.commit()

    return AccountDeletionStatus(
        deletion_due_at=user.deletion_due_at,
        message="Account deletion scheduled. All sessions have been revoked. Local device data has not been deleted.",
    )


async def cancel_deletion(
    session: AsyncSession,
    token: str,
    *,
    client_type: str = "unknown",
    device_label: str | None = None,
    user_agent: str | None = None,
) -> AuthResult:
    """Consume the recovery proof and restore access with a new session in one transaction."""
    user, _ = await _lock_authorization(session, token, AUTHORIZATION_PURPOSE)

    if user.deletion_due_at is None:
        raise ValidationError("No account deletion is scheduled")

    user.deletion_requested_at = None
    user.deletion_due_at = None
    user.disabled_at = None
    await _revoke_credentials(session, user.user_id)
    result = await _create_session_for_user(session, user, client_type, device_label, user_agent)
    await queue_account_notification(session, user)
    await session.commit()
    return result


async def purge_due_accounts(session: AsyncSession) -> int:
    """Commit cascade deletion and cleanup work atomically before touching physical files."""
    users = (
        await session.scalars(
            select(User).where(User.deletion_due_at <= _now()).with_for_update(skip_locked=True).limit(100)
        )
    ).all()

    for user in users:
        session.add(AccountMediaCleanup(user_id=user.user_id))
        await session.execute(delete(User).where(User.user_id == user.user_id))

    await session.commit()
    return len(users)


def _remove_owner_files(user_id: UUID) -> None:
    """Only remove the UUID-scoped directories, never follow an owner symlink."""
    roots = [media_root()]
    import_root = get_settings().acquisition_import_root

    if import_root is not None:
        roots.append(Path(import_root))

    for root in roots:
        path = root / str(user_id)

        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)


async def cleanup_deleted_media(session: AsyncSession) -> int:
    cleaned = 0
    user_ids = (await session.scalars(select(AccountMediaCleanup.user_id).limit(100))).all()
    await session.commit()

    for user_id in user_ids:
        try:
            job = await session.scalar(
                select(AccountMediaCleanup)
                .where(AccountMediaCleanup.user_id == user_id)
                .with_for_update(skip_locked=True)
            )

            if job is None:
                await session.rollback()
                continue

            await asyncio.to_thread(_remove_owner_files, user_id)
            await session.delete(job)
            await session.commit()
            cleaned += 1
        except Exception:
            await session.rollback()
            logger.exception("Account media cleanup failed and will be retried")

    return cleaned


async def run_cleanup(session_maker: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            async with session_maker() as session:
                await purge_due_accounts(session)
                await cleanup_deleted_media(session)
                await deliver_pending_notifications(session)
        except Exception:
            logger.exception("Account deletion cleanup cycle failed and will be retried")

        await asyncio.sleep(60)
