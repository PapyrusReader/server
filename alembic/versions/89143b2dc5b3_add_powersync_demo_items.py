"""add powersync demo items

Revision ID: 89143b2dc5b3
Revises: 537c0d8fc9bb
Create Date: 2026-03-28 17:01:22.422984

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "89143b2dc5b3"
down_revision: str | Sequence[str] | None = "537c0d8fc9bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "powersync_demo_items",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index(
        op.f("ix_powersync_demo_items_owner_user_id"),
        "powersync_demo_items",
        ["owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_powersync_demo_items_owner_user_id"), table_name="powersync_demo_items")
    op.drop_table("powersync_demo_items")
