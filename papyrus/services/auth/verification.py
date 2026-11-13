"""Email-bound verification evidence and a dedicated transactional delivery outbox."""

import asyncio
import logging
import math
from datetime import timedelta
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.config import get_settings
from papyrus.core.exceptions import VerificationRequiredError, VerificationUnavailableError
from papyrus.core.security import generate_opaque_token, hash_opaque_token
from papyrus.models import EmailActionToken, User, VerificationDelivery, VerificationRecipient
from papyrus.services import email
from papyrus.services.auth._core import _build_app_url, _now
from papyrus.services.auth.types import EMAIL_VERIFICATION_ACTION

COOLDOWN_SECONDS = 60
TOKEN_LIFETIME = timedelta(hours=24)
logger = logging.getLogger(__name__)


def require_delivery_configuration() -> None:
    base = get_settings().app_public_base_url
    parsed = urlsplit(base or "")

    if not email.is_email_delivery_configured() or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VerificationUnavailableError()


def accepts_verification(user: User) -> bool:
    if user.primary_email is None:
        return False

    if user.deletion_due_at is not None:
        return user.deletion_due_at > _now()

    return user.disabled_at is None


def record_verification(user: User, source: Literal["papyrus_email", "google"]) -> None:
    if user.primary_email is None:
        raise ValueError("Verification requires a primary email")

    user.email_verification_source = source
    user.email_verified_at = _now()


async def require_verified(session: AsyncSession, user: User) -> None:
    if not user.email_verified:
        raise await verification_required(session, user)


async def verification_required(session: AsyncSession, user: User) -> VerificationRequiredError:
    recipient = await session.get(VerificationRecipient, user.primary_email) if user.primary_email else None
    remaining = 0

    if recipient is not None:
        remaining = max(
            0, math.ceil((recipient.last_queued_at + timedelta(seconds=COOLDOWN_SECONDS) - _now()).total_seconds())
        )

    return VerificationRequiredError(user.primary_email, remaining)


async def queue_verification(session: AsyncSession, user: User) -> bool:
    """The caller holds the owner lock and commits account, token and delivery together."""
    require_delivery_configuration()

    if user.email_verified or not accepts_verification(user):
        return False

    now = _now()

    claimed = await session.scalar(
        insert(VerificationRecipient)
        .values(email=user.primary_email, last_queued_at=now)
        .on_conflict_do_update(
            index_elements=[VerificationRecipient.email],
            set_={"last_queued_at": now},
            where=VerificationRecipient.last_queued_at <= now - timedelta(seconds=COOLDOWN_SECONDS),
        )
        .returning(VerificationRecipient.email)
    )

    if claimed is None:
        return False

    token = generate_opaque_token()
    expires_at = now + TOKEN_LIFETIME

    action = EmailActionToken(
        user_id=user.user_id,
        email=user.primary_email,
        action_type=EMAIL_VERIFICATION_ACTION,
        token_hash=hash_opaque_token(token),
        expires_at=expires_at,
    )

    session.add(action)
    await session.flush()

    session.add(
        VerificationDelivery(
            token_id=action.token_id,
            token=token,
            next_attempt_at=now,
        ),
    )

    await session.flush()
    return True


async def cancel_verification_deliveries(session: AsyncSession, user: User) -> None:
    tokens = select(EmailActionToken.token_id).where(
        EmailActionToken.user_id == user.user_id,
        EmailActionToken.email == user.primary_email,
        EmailActionToken.action_type == EMAIL_VERIFICATION_ACTION,
    )

    await session.execute(delete(VerificationDelivery).where(VerificationDelivery.token_id.in_(tokens)))

    await session.execute(
        update(EmailActionToken)
        .where(EmailActionToken.token_id.in_(tokens), EmailActionToken.used_at.is_(None))
        .values(used_at=_now()),
    )


async def deliver_pending_verifications(session: AsyncSession) -> int:
    """Lease before SMTP. Duplicate delivery keeps the original token and expiry.

    A send already accepted by SMTP cannot be recalled after concurrent verification
    or deletion. Its token is invalidated by those transitions and cannot grant access.
    Neither SMTP exceptions nor SQL parameters containing secrets are logged.
    """
    valid_token = select(EmailActionToken.token_id).where(
        EmailActionToken.used_at.is_(None),
        EmailActionToken.expires_at > _now(),
        EmailActionToken.email == User.primary_email,
        EmailActionToken.user_id == User.user_id,
        ((User.disabled_at.is_(None) & User.deletion_due_at.is_(None)) | (User.deletion_due_at > _now())),
    )

    await session.execute(
        delete(VerificationDelivery).where(
            VerificationDelivery.token_id.not_in(valid_token),
        ),
    )

    await session.execute(
        delete(VerificationRecipient).where(
            VerificationRecipient.last_queued_at <= _now() - timedelta(seconds=COOLDOWN_SECONDS),
        ),
    )

    await session.commit()

    try:
        require_delivery_configuration()
    except VerificationUnavailableError:
        return 0

    delivered = 0

    for _ in range(100):
        now = _now()

        result = await session.execute(
            select(VerificationDelivery, EmailActionToken)
            .join(EmailActionToken, EmailActionToken.token_id == VerificationDelivery.token_id)
            .where(VerificationDelivery.next_attempt_at <= now, EmailActionToken.expires_at > now)
            .order_by(VerificationDelivery.next_attempt_at)
            .with_for_update(of=VerificationDelivery, skip_locked=True)
            .limit(1),
        )

        delivery = result.one_or_none()

        if delivery is None:
            await session.rollback()
            break

        job, action = delivery
        assert action.email is not None
        job.attempts += 1
        job.next_attempt_at = min(now + timedelta(minutes=5), action.expires_at)
        await session.commit()
        url = _build_app_url("/verify-email", {"token": job.token})
        subject = "Verify your Papyrus email address"
        body = f"Verify your Papyrus email address.\n\n{url}\n\nThis link expires 24 hours after it was requested."

        try:
            html = email.render_email_html(
                subject=subject,
                preheader="Verify your email address to sign in to Papyrus.",
                category="ACCOUNT SECURITY",
                heading="Verify your email address",
                greeting="Hello,",
                summary="Verify your email address to sign in to Papyrus.",
                guidance="This link expires 24 hours after it was requested and can only be used once.",
                security_note="If you didn't request this email, you can ignore it.",
                action_label="Verify email",
                action_url=url,
            )

            await asyncio.to_thread(email.send_email, action.email, subject, body, html_body=html)
        except Exception:
            retry_at = min(_now() + timedelta(seconds=min(60 * 2 ** min(job.attempts - 1, 6), 3600)), action.expires_at)

            await session.execute(
                update(VerificationDelivery)
                .where(VerificationDelivery.token_id == job.token_id)
                .values(next_attempt_at=retry_at),
            )

            await session.commit()
            logger.warning("Verification delivery failed and will be retried")
            continue

        await session.execute(delete(VerificationDelivery).where(VerificationDelivery.token_id == job.token_id))
        await session.commit()
        delivered += 1

    return delivered


async def run_delivery(session_maker: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            async with session_maker() as session:
                await deliver_pending_verifications(session)
        except Exception:
            logger.warning("Verification delivery cycle failed and will be retried")

        await asyncio.sleep(30)
