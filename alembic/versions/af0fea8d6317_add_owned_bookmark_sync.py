"""add owned bookmark sync

Revision ID: af0fea8d6317
Revises: dcd3b384e6a4
Create Date: 2026-09-06 00:15:06.254846

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "af0fea8d6317"
down_revision: str | Sequence[str] | None = "dcd3b384e6a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add owned bookmarks without changing existing library data."""
    op.create_table(
        "bookmarks",
        sa.Column("bookmark_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Float(), server_default="0", nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("chapter_title", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("color_hex", sa.Text(), server_default="#FF5722", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("position >= 0 AND position <= 1", name="ck_bookmarks_position_range"),
        sa.ForeignKeyConstraint(["book_id"], ["books.book_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bookmark_id"),
    )
    op.create_index(op.f("ix_bookmarks_book_id"), "bookmarks", ["book_id"], unique=False)
    op.create_index(op.f("ix_bookmarks_owner_user_id"), "bookmarks", ["owner_user_id"], unique=False)


def downgrade() -> None:
    """Remove bookmark storage; export bookmarks before downgrading."""
    op.drop_index(op.f("ix_bookmarks_owner_user_id"), table_name="bookmarks")
    op.drop_index(op.f("ix_bookmarks_book_id"), table_name="bookmarks")
    op.drop_table("bookmarks")
