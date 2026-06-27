"""add media assets

Revision ID: c3f8b2a9d1e4
Revises: a1d7c2f4e8b9
Create Date: 2026-06-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8b2a9d1e4"
down_revision: str | Sequence[str] | None = "a1d7c2f4e8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("books", sa.Column("file_media_id", sa.Uuid(), nullable=True))
    op.add_column("books", sa.Column("cover_media_id", sa.Uuid(), nullable=True))

    op.create_table(
        "media_assets",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.book_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("asset_id"),
    )
    op.create_index(op.f("ix_media_assets_book_id"), "media_assets", ["book_id"], unique=False)
    op.create_index(op.f("ix_media_assets_owner_user_id"), "media_assets", ["owner_user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_media_assets_owner_user_id"), table_name="media_assets")
    op.drop_index(op.f("ix_media_assets_book_id"), table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_column("books", "cover_media_id")
    op.drop_column("books", "file_media_id")
