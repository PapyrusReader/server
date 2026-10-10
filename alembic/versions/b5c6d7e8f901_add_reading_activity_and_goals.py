"""Add owned, durable goals and append-only activity without changing book data."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b5c6d7e8f901"
down_revision: str | Sequence[str] | None = "af0fea8d6317"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("reading_goals", "reading_activities", "goal_periods"):
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("owner_user_id", sa.Uuid(), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(f"ix_{table}_owner_user_id", table, ["owner_user_id"])


def downgrade() -> None:
    for table in ("goal_periods", "reading_activities", "reading_goals"):
        op.drop_index(f"ix_{table}_owner_user_id", table_name=table)
        op.drop_table(table)
