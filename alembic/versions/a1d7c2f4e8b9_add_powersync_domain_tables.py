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

    op.create_table(
        "annotations",
        sa.Column("annotation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("selected_text", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("highlight_color", sa.String(length=16), server_default="#FFEB3B", nullable=False),
        sa.Column("start_position", sa.Text(), nullable=False),
        sa.Column("end_position", sa.Text(), nullable=False),
        sa.Column("chapter_title", sa.String(length=255), nullable=True),
        sa.Column("chapter_index", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.book_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("annotation_id"),
    )
    op.create_index(op.f("ix_annotations_book_id"), "annotations", ["book_id"], unique=False)
    op.create_index(op.f("ix_annotations_owner_user_id"), "annotations", ["owner_user_id"], unique=False)

    op.create_table(
        "reading_sessions",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_position", sa.Float(), nullable=True),
        sa.Column("end_position", sa.Float(), nullable=True),
        sa.Column("pages_read", sa.Integer(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("device_type", sa.String(length=64), nullable=True),
        sa.Column("device_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.book_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(op.f("ix_reading_sessions_book_id"), "reading_sessions", ["book_id"], unique=False)
    op.create_index(op.f("ix_reading_sessions_owner_user_id"), "reading_sessions", ["owner_user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_reading_sessions_owner_user_id"), table_name="reading_sessions")
    op.drop_index(op.f("ix_reading_sessions_book_id"), table_name="reading_sessions")
    op.drop_table("reading_sessions")

    op.drop_index(op.f("ix_annotations_owner_user_id"), table_name="annotations")
    op.drop_index(op.f("ix_annotations_book_id"), table_name="annotations")
    op.drop_table("annotations")

    op.drop_index(op.f("ix_books_owner_user_id"), table_name="books")
    op.drop_table("books")
