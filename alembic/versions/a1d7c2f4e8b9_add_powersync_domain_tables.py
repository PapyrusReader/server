"""add powersync domain tables

Revision ID: a1d7c2f4e8b9
Revises: 89143b2dc5b3
Create Date: 2026-05-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1d7c2f4e8b9"
down_revision: str | Sequence[str] | None = "89143b2dc5b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "books",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("subtitle", sa.String(length=500), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("co_authors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("isbn", sa.String(length=32), nullable=True),
        sa.Column("isbn13", sa.String(length=32), nullable=True),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cover_image_url", sa.String(length=2048), nullable=True),
        sa.Column("reading_status", sa.String(length=32), nullable=True),
        sa.Column("current_page", sa.Integer(), nullable=True),
        sa.Column("current_position", sa.Float(), nullable=True),
        sa.Column("current_cfi", sa.Text(), nullable=True),
        sa.Column("is_favorite", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("custom_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("book_id"),
    )
    op.create_index(op.f("ix_books_owner_user_id"), "books", ["owner_user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_books_owner_user_id"), table_name="books")
    op.drop_table("books")
