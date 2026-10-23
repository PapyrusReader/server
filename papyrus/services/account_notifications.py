"""Branded account notices and a transactional, retryable delivery queue."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

from sqlalchemy import delete, exists, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from papyrus.config import get_settings
from papyrus.models import AccountNotification, User
from papyrus.services import email

logger = logging.getLogger(__name__)
NOTICE_LIFETIME = timedelta(days=7)
DELIVERY_LEASE = timedelta(minutes=5)


@dataclass(frozen=True)
class AccountEmail:
    subject: str
    text: str
    html: str


def _sign_in_url() -> str | None:
    settings = get_settings()

    if settings.app_public_base_url:
        url = f"{settings.app_public_base_url.rstrip('/')}/login"
    elif settings.public_base_url:
        url = f"{settings.public_base_url.rstrip('/')}{settings.api_prefix}/account-deletion"
    else:
        return None

    parsed = urlsplit(url)
    return url if parsed.scheme in {"https", "http"} and parsed.netloc else None


def render_account_email(display_name: str, deletion_due_at: datetime | None) -> AccountEmail:
    """Keep email links informational: recovery still requires sign-in and explicit consent."""
    greeting = f"Hi {display_name}," if display_name else "Hello,"
    deadline_html = ""
    deadline_text = ""

    if deletion_due_at is not None:
        deadline = deletion_due_at.astimezone(UTC).strftime("%B %d, %Y at %H:%M UTC")
        subject = "Your Papyrus account is scheduled for deletion"
        heading = "Your account is scheduled for deletion"
        preheader = f"You can keep your account until {deadline}."
        summary = (
            "We received your request to delete your Papyrus account. "
            "Your profile, synced library and reading data will be permanently deleted after the deadline below."
        )

        guidance = "Changed your mind? Sign in before this date and choose Keep account."

        security_note = (
            "If you didn't request this, sign in and choose Keep account before the deadline, "
            "then secure the password or Google account you use to sign in."
        )

        action_label = "Sign in to keep your account"
        deadline_text = f"Recovery deadline: {deadline}\n\n"
        deadline_html = (
            '<div style="margin:0 0 24px;padding:20px;background:#f3f1fb;border-radius:10px;">'
            '<p style="margin:0 0 8px;font-size:12px;letter-spacing:1px;color:#645f73;">RECOVERY DEADLINE</p>'
            f'<p style="margin:0;font-size:18px;line-height:28px;font-weight:700;">{escape(deadline)}</p></div>'
        )
    else:
        subject = "Your Papyrus account deletion was cancelled"
        heading = "Your account is staying"
        preheader = "Deletion has been cancelled. Your library and reading data are still here."
        summary = "Your account deletion request has been cancelled. Your profile, library and reading data are safe."
        guidance = "You can continue using Papyrus as usual. No further action is needed."

        security_note = (
            "If you didn't keep this account, secure the password or Google account you use to sign in. "
            "You can review your account in Papyrus."
        )

        action_label = "Open Papyrus"

    url = _sign_in_url()
    action_html = ""
    action_text = ""

    if url is not None:
        escaped_url = escape(url, quote=True)
        action_text = f"{action_label}: {url}\n\n"

        action_html = (
            '<table role="presentation" cellspacing="0" cellpadding="0"><tr>'
            '<td style="border-radius:8px;background:#5654a8;text-align:center;">'
            f'<a href="{escaped_url}" style="display:inline-block;padding:14px 24px;border:1px solid #5654a8;'
            'border-radius:8px;color:#ffffff;font-size:16px;line-height:24px;font-weight:700;text-decoration:none;">'
            f"{escape(action_label)}</a></td></tr></table>"
            '<p style="margin:16px 0 0;font-size:12px;line-height:20px;color:#726c86;overflow-wrap:anywhere;">'
            f'Or open <a href="{escaped_url}" style="color:#5654a8;word-break:break-all;">{escaped_url}</a></p>'
        )

    template = Template((Path(__file__).parents[1] / "templates" / "account_notification.html").read_text())

    html = template.substitute(
        subject=escape(subject),
        preheader=escape(preheader),
        heading=escape(heading),
        greeting=escape(greeting),
        summary=escape(summary),
        deadline=deadline_html,
        guidance=escape(guidance),
        action=action_html,
        security_note=escape(security_note),
    )

    text = (
        f"Papyrus\n\n{heading}\n\n{greeting}\n\n{summary}\n\n"
        f"{deadline_text}{guidance}\n\n{action_text}{security_note}\n\n"
        "This is an automatic account notification from Papyrus."
    )

    return AccountEmail(subject=subject, text=text, html=html)


async def queue_account_notification(session: AsyncSession, user: User) -> None:
    """Keep a notice for each committed lifecycle event, including rapid deletion and recovery."""
    if not user.primary_email or not user.primary_email_verified:
        return

    content = render_account_email(user.display_name, user.deletion_due_at)
    now = datetime.now(UTC)
    expires_at = now + NOTICE_LIFETIME

    if user.deletion_due_at is not None:
        expires_at = min(expires_at, user.deletion_due_at)

    session.add(
        AccountNotification(
            user_id=user.user_id,
            recipient=user.primary_email,
            subject=content.subject,
            text_body=content.text,
            html_body=content.html,
            created_at=now,
            next_attempt_at=now,
            expires_at=expires_at,
        )
    )


async def deliver_pending_notifications(session: AsyncSession) -> int:
    """Lease before SMTP so delivery never holds account locks or blocks recovery.

    SMTP is at-least-once: a crash after acceptance but before deletion can repeat
    a notice. Later notices for the same account wait for earlier deliveries or
    their expiry. Pending messages expire after seven days and contain no auth tokens.
    """
    await session.execute(delete(AccountNotification).where(AccountNotification.expires_at <= datetime.now(UTC)))
    await session.commit()

    if not email.is_email_delivery_configured():
        return 0

    delivered = 0
    earlier = aliased(AccountNotification)

    has_earlier_notice = exists().where(
        earlier.user_id == AccountNotification.user_id,
        tuple_(earlier.created_at, earlier.notification_id)
        < tuple_(AccountNotification.created_at, AccountNotification.notification_id),
    )

    for _ in range(100):
        now = datetime.now(UTC)

        notice = await session.scalar(
            select(AccountNotification)
            .where(
                AccountNotification.next_attempt_at <= now,
                AccountNotification.expires_at > now,
                ~has_earlier_notice,
            )
            .order_by(AccountNotification.created_at, AccountNotification.notification_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )

        if notice is None:
            await session.rollback()
            break

        notice.attempts += 1
        notice.next_attempt_at = now + DELIVERY_LEASE
        await session.commit()

        try:
            await asyncio.to_thread(
                email.send_email,
                notice.recipient,
                notice.subject,
                notice.text_body,
                html_body=notice.html_body,
            )
        except Exception:
            retry_at = datetime.now(UTC) + timedelta(seconds=min(60 * 2 ** min(notice.attempts - 1, 6), 3600))

            await session.execute(
                update(AccountNotification)
                .where(AccountNotification.notification_id == notice.notification_id)
                .values(next_attempt_at=retry_at)
            )

            await session.commit()
            logger.warning("Account notification delivery failed and will be retried")
            continue

        await session.execute(
            delete(AccountNotification).where(AccountNotification.notification_id == notice.notification_id)
        )

        await session.commit()
        delivered += 1

    return delivered
