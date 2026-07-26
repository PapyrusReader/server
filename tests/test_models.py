"""Model metadata registration tests."""

from sqlalchemy import UniqueConstraint

from papyrus.models import (
    AcquisitionEndpoint,
    AcquisitionJob,
    AuthExchangeCode,
    AuthSession,
    Base,
    EmailActionToken,
    MediaAsset,
    PasswordCredential,
    PowerSyncDemoItem,
    SyncBook,
    User,
    UserIdentity,
)


def test_managed_acquisition_models_expose_download_lifecycle() -> None:
    endpoint_table = AcquisitionEndpoint.__table__
    job_table = AcquisitionJob.__table__

    assert "download_root" in endpoint_table.columns
    assert endpoint_table.c.download_root.nullable is True

    assert {
        "book_id",
        "client_hash",
        "client_state",
        "progress_basis_points",
        "downloaded_bytes",
        "total_bytes",
        "download_speed_bytes_per_second",
        "eta_seconds",
        "selected_file_path",
        "retry_count",
        "next_poll_at",
        "lease_owner",
        "lease_until",
        "submitted_at",
        "started_at",
        "updated_at",
        "completed_at",
        "cancelled_at",
    }.issubset(job_table.columns.keys())
    assert job_table.c.download_url.nullable is True
    assert {index.name for index in job_table.indexes}.issuperset(
        {
            "ix_acquisition_jobs_owner_status",
            "ix_acquisition_jobs_next_poll_at",
        }
    )
    assert any(
        foreign_key.target_fullname == "books.book_id"
        for foreign_key in job_table.c.book_id.foreign_keys
    )


def test_media_asset_kind_is_unique_per_book() -> None:
    """Ensure concurrent replacements cannot leave duplicate book media."""
    table = MediaAsset.__table__
    unique_columns = {
        tuple(constraint.columns.keys()) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    }

    assert ("book_id", "kind") in unique_columns


def test_auth_models_are_registered_with_metadata() -> None:
    """Ensure Alembic can discover the auth-related tables through Base.metadata."""
    users_table = Base.metadata.tables["users"]
    identities_table = Base.metadata.tables["user_identities"]
    password_credentials_table = Base.metadata.tables["password_credentials"]
    sessions_table = Base.metadata.tables["auth_sessions"]
    exchange_codes_table = Base.metadata.tables["auth_exchange_codes"]
    email_tokens_table = Base.metadata.tables["email_action_tokens"]
    powersync_demo_items_table = Base.metadata.tables["powersync_demo_items"]
    books_table = Base.metadata.tables["books"]
    assert users_table is User.__table__
    assert identities_table is UserIdentity.__table__
    assert password_credentials_table is PasswordCredential.__table__
    assert sessions_table is AuthSession.__table__
    assert exchange_codes_table is AuthExchangeCode.__table__
    assert email_tokens_table is EmailActionToken.__table__
    assert powersync_demo_items_table is PowerSyncDemoItem.__table__
    assert books_table is SyncBook.__table__

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
    assert set(powersync_demo_items_table.columns.keys()) == {
        "item_id",
        "owner_user_id",
        "title",
        "notes",
        "created_at",
        "updated_at",
    }
    assert {"book_id", "owner_user_id", "title", "updated_at"}.issubset(books_table.columns.keys())
    assert "annotations" not in Base.metadata.tables
    assert "reading_sessions" not in Base.metadata.tables
