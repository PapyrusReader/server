"""expand auth schema

Revision ID: 537c0d8fc9bb
Revises: 760acf37dec0
Create Date: 2026-03-28 00:43:39.774081

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "537c0d8fc9bb"
down_revision: str | Sequence[str] | None = "760acf37dec0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("email", new_column_name="primary_email", existing_type=sa.String(length=255))
        batch_op.alter_column(
            "email_verified",
            new_column_name="primary_email_verified",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            existing_server_default=sa.text("false"),
        )
        batch_op.add_column(sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.drop_column("is_anonymous")

    op.create_table(
        "user_identities",
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("email_at_provider", sa.String(length=255), nullable=True),
        sa.Column("email_verified_at_provider", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("identity_id"),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_user_identities_provider_subject"),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])

    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute(
        """
        INSERT INTO password_credentials (user_id, password_hash, password_changed_at)
        SELECT user_id, password_hash, COALESCE(created_at, now())
        FROM users
        WHERE password_hash IS NOT NULL
        """
    )

    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("client_type", sa.String(length=32), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("device_label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "auth_exchange_codes",
        sa.Column("code_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("provider_subject", sa.String(length=255), nullable=True),
        sa.Column("email_at_provider", sa.String(length=255), nullable=True),
        sa.Column("email_verified_at_provider", sa.DateTime(timezone=True), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("code_id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_auth_exchange_codes_user_id", "auth_exchange_codes", ["user_id"])

    op.create_table(
        "email_action_tokens",
        sa.Column("token_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_email_action_tokens_user_id", "email_action_tokens", ["user_id"])

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("password_hash")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))

    op.execute(
        """
        UPDATE users
        SET password_hash = password_credentials.password_hash
        FROM password_credentials
        WHERE users.user_id = password_credentials.user_id
        """
    )

    op.drop_index("ix_email_action_tokens_user_id", table_name="email_action_tokens")
    op.drop_table("email_action_tokens")

    op.drop_index("ix_auth_exchange_codes_user_id", table_name="auth_exchange_codes")
    op.drop_table("auth_exchange_codes")

    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_table("password_credentials")

    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("is_anonymous", sa.Boolean(), server_default=sa.text("false"), nullable=False))
        batch_op.drop_column("disabled_at")
        batch_op.alter_column(
            "primary_email_verified",
            new_column_name="email_verified",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            existing_server_default=sa.text("false"),
        )
        batch_op.alter_column("primary_email", new_column_name="email", existing_type=sa.String(length=255))
