"""Authentication-related schemas."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

ClientType = Literal["mobile", "desktop", "web", "unknown"]


class RegisterRequest(BaseModel):
    """User registration request."""

    email: EmailStr = Field(..., examples=["user@example.com"])
    password: str = Field(..., min_length=8, examples=["SecureP@ss123"])
    display_name: str = Field(..., min_length=1, max_length=100, examples=["John Doe"])
    client_type: ClientType = "unknown"
    device_label: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    """User login request."""

    email: EmailStr
    password: str
    client_type: ClientType = "unknown"
    device_label: str | None = Field(default=None, max_length=255)


class OAuthStartRequest(BaseModel):
    """OAuth flow bootstrap request."""

    redirect_uri: str = Field(..., description="App callback URI for the browser flow")


class AuthorizationUrlResponse(BaseModel):
    """Authorization URL returned to the client for browser login."""

    authorization_url: str


class OAuthExchangeRequest(BaseModel):
    """Exchange a one-time code for Papyrus tokens."""

    code: str = Field(..., description="One-time exchange code returned from the browser callback")
    client_type: ClientType = "unknown"
    device_label: str | None = Field(default=None, max_length=255)


class OAuthLinkCompleteRequest(BaseModel):
    """Consume a one-time exchange code to link a provider."""

    code: str = Field(..., description="One-time exchange code returned from the browser callback")


class RefreshTokenRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str = Field(..., description="Refresh token from login")


class AuthTokens(BaseModel):
    """Authentication tokens response."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(..., description="Access token expiry in seconds", examples=[3600])
    user: "User"


class PowerSyncTokenResponse(BaseModel):
    """Short-lived PowerSync token response."""

    token: str
    expires_in: int


class LogoutRequest(BaseModel):
    """Logout request."""

    all_devices: bool = Field(default=False, description="Logout from all devices")


class VerifyEmailRequest(BaseModel):
    """Email verification request."""

    token: str = Field(..., description="Email verification token")


class ResendVerificationRequest(BaseModel):
    """Resend verification email request."""

    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    """Password recovery request."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Password reset request."""

    token: str = Field(..., description="Password reset token")
    password: str = Field(..., min_length=8, description="New password")


class ChangePasswordRequest(BaseModel):
    """Password change request."""

    current_password: str
    new_password: str = Field(..., min_length=8)


# Import User here to avoid circular import
from papyrus.schemas.user import User  # noqa: E402

AuthTokens.model_rebuild()
