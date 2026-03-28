"""Public types and constants for the auth service package."""

from __future__ import annotations

from dataclasses import dataclass

from papyrus.models import User

GOOGLE_PROVIDER = "google"
EMAIL_VERIFICATION_ACTION = "verify_email"
PASSWORD_RESET_ACTION = "reset_password"


@dataclass(slots=True)
class AuthResult:
    """Authentication result returned by the service layer."""

    access_token: str
    refresh_token: str
    expires_in: int
    user: User


@dataclass(slots=True)
class GoogleIdentity:
    """Resolved Google identity from OAuth callback processing."""

    subject: str
    email: str | None
    email_verified: bool
    display_name: str | None
    avatar_url: str | None
