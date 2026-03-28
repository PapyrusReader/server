"""Authentication service façade."""

from papyrus.services.auth.email_flows import (
    begin_password_reset,
    resend_verification_email,
    reset_password,
    verify_email_token,
)
from papyrus.services.auth.google import (
    GoogleOAuthClient,
    build_google_link_authorization_url,
    build_google_login_authorization_url,
    complete_google_link,
    google_oauth_client,
    handle_google_callback,
)
from papyrus.services.auth.powersync import create_powersync_credentials, get_powersync_jwks_payload
from papyrus.services.auth.sessions import (
    exchange_login_code,
    login_user,
    logout_all_sessions,
    logout_current_session,
    refresh_tokens,
    register_user,
)
from papyrus.services.auth.types import (
    EMAIL_VERIFICATION_ACTION,
    GOOGLE_PROVIDER,
    PASSWORD_RESET_ACTION,
    AuthResult,
    GoogleIdentity,
)

__all__ = [
    "AuthResult",
    "GoogleIdentity",
    "GoogleOAuthClient",
    "GOOGLE_PROVIDER",
    "EMAIL_VERIFICATION_ACTION",
    "PASSWORD_RESET_ACTION",
    "google_oauth_client",
    "register_user",
    "login_user",
    "refresh_tokens",
    "logout_current_session",
    "logout_all_sessions",
    "build_google_login_authorization_url",
    "build_google_link_authorization_url",
    "handle_google_callback",
    "exchange_login_code",
    "complete_google_link",
    "create_powersync_credentials",
    "get_powersync_jwks_payload",
    "resend_verification_email",
    "verify_email_token",
    "begin_password_reset",
    "reset_password",
]
