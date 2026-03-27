"""Model metadata registration tests."""

from papyrus.models import (
    AuthExchangeCode,
    AuthSession,
    Base,
    EmailActionToken,
    PasswordCredential,
    User,
    UserIdentity,
)


def test_auth_models_are_registered_with_metadata() -> None:
    """Ensure Alembic can discover the auth-related tables through Base.metadata."""
    users_table = Base.metadata.tables["users"]
    identities_table = Base.metadata.tables["user_identities"]
    password_credentials_table = Base.metadata.tables["password_credentials"]
    sessions_table = Base.metadata.tables["auth_sessions"]
    exchange_codes_table = Base.metadata.tables["auth_exchange_codes"]
    email_tokens_table = Base.metadata.tables["email_action_tokens"]

    assert users_table is User.__table__
    assert identities_table is UserIdentity.__table__
    assert password_credentials_table is PasswordCredential.__table__
    assert sessions_table is AuthSession.__table__
    assert exchange_codes_table is AuthExchangeCode.__table__
    assert email_tokens_table is EmailActionToken.__table__

    assert set(users_table.columns.keys()) == {
        "user_id",
        "display_name",
        "avatar_url",
        "primary_email",
        "primary_email_verified",
        "created_at",
        "last_login_at",
        "disabled_at",
    }
