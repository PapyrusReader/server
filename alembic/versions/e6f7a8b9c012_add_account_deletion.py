"""Add scheduled account deletion and durable media cleanup."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6f7a8b9c012"
down_revision: str | Sequence[str] | None = "b5c6d7e8f901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deletion_due_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_deletion_due_at", "users", ["deletion_due_at"])
    op.add_column("auth_exchange_codes", sa.Column("code_challenge", sa.String(64), nullable=True))

    op.create_table(
        "account_media_cleanup",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("account_media_cleanup")
    op.drop_column("auth_exchange_codes", "code_challenge")
    op.drop_index("ix_users_deletion_due_at", table_name="users")
    op.drop_column("users", "deletion_due_at")
    op.drop_column("users", "deletion_requested_at")
