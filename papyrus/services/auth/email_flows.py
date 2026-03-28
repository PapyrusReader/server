"""Email verification and password reset service functions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.security import hash_password
from papyrus.models import PasswordCredential
from papyrus.services import email as email_service
from papyrus.services.auth._core import (
    _consume_email_action_token,
    _get_active_user,
    _get_user_by_email,
    _issue_email_action_token,
    _normalize_email,
    _now,
    _password_reset_email_body,
    _revoke_user_sessions,
    _verification_email_body,
)
from papyrus.services.auth.types import EMAIL_VERIFICATION_ACTION, PASSWORD_RESET_ACTION


async def resend_verification_email(session: AsyncSession, email: str) -> str:
    normalized_email = _normalize_email(email)
    user = await _get_user_by_email(session, normalized_email)

    if user is None or user.primary_email_verified:
        return "If the email is registered, a verification link has been sent"

    if not email_service.is_email_delivery_configured():
        return "Email verification is not configured on this server"

    token = await _issue_email_action_token(
        session,
        user.user_id,
        EMAIL_VERIFICATION_ACTION,
        get_settings().email_verification_token_expire_minutes,
    )

    email_service.send_email(
        normalized_email,
        "Verify your Papyrus email address",
        _verification_email_body(token),
    )

    await session.commit()
    return "If the email is registered, a verification link has been sent"


async def verify_email_token(session: AsyncSession, token: str) -> str:
    email_token = await _consume_email_action_token(session, token, EMAIL_VERIFICATION_ACTION)
    user = await _get_active_user(session, email_token.user_id)
    user.primary_email_verified = True
    await session.commit()
    return "Email verified successfully"


async def begin_password_reset(session: AsyncSession, email: str) -> str:
    normalized_email = _normalize_email(email)
    user = await _get_user_by_email(session, normalized_email)

    if user is None:
        return "If the email is registered, a reset link has been sent"

    if not email_service.is_email_delivery_configured():
        return "Password reset is not configured on this server"

    token = await _issue_email_action_token(
        session,
        user.user_id,
        PASSWORD_RESET_ACTION,
        get_settings().password_reset_token_expire_minutes,
    )

    email_service.send_email(
        normalized_email,
        "Reset your Papyrus password",
        _password_reset_email_body(token),
    )

    await session.commit()
    return "If the email is registered, a reset link has been sent"


async def reset_password(session: AsyncSession, token: str, new_password: str) -> str:
    email_token = await _consume_email_action_token(session, token, PASSWORD_RESET_ACTION)
    user = await _get_active_user(session, email_token.user_id)

    credential_result = await session.execute(
        select(PasswordCredential).where(PasswordCredential.user_id == user.user_id)
    )

    credential = credential_result.scalar_one_or_none()

    if credential is None:
        credential = PasswordCredential(user_id=user.user_id, password_hash=hash_password(new_password))
        session.add(credential)
    else:
        credential.password_hash = hash_password(new_password)
        credential.password_changed_at = _now()

    await _revoke_user_sessions(session, user.user_id)
    await session.commit()
    return "Password has been reset successfully"
