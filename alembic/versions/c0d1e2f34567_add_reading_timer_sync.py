"""Add account-owned active timer state without changing reading history."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c0d1e2f34567"
down_revision: str | Sequence[str] | None = "b9c0d1e2f345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reading_timers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("base_version", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('active', 'saved', 'discarded')", name="reading_timers_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reading_timers_owner_user_id", "reading_timers", ["owner_user_id"])

    op.create_index(
        "uq_reading_timers_active_owner",
        "reading_timers",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("reading_timers")
