"""add owned library sync and promoted book metadata

Revision ID: dcd3b384e6a4
Revises: a0c1456470b0
Create Date: 2026-09-05 22:48:08.042605

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "dcd3b384e6a4"
down_revision: str | Sequence[str] | None = "a0c1456470b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "shelves",
        sa.Column("shelf_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color_hex", sa.Text(), nullable=True),
        sa.Column("icon_code_point", sa.Integer(), nullable=True),
        sa.Column("icon_font_family", sa.Text(), nullable=True),
        sa.Column("icon_font_package", sa.Text(), nullable=True),
        sa.Column("icon_match_text_direction", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("parent_shelf_id", sa.Uuid(), nullable=True),
        sa.Column("is_smart", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("smart_query", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_shelf_id"], ["shelves.shelf_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("shelf_id"),
    )
    op.create_index(op.f("ix_shelves_owner_user_id"), "shelves", ["owner_user_id"], unique=False)
    op.create_table(
        "sync_tombstones",
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("table_name", "entity_id"),
    )
    op.create_index(op.f("ix_sync_tombstones_owner_user_id"), "sync_tombstones", ["owner_user_id"], unique=False)
    op.create_table(
        "tags",
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color_hex", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tag_id"),
    )
    op.create_index(op.f("ix_tags_owner_user_id"), "tags", ["owner_user_id"], unique=False)
    op.create_table(
        "annotations",
        sa.Column("annotation_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("selected_text", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), server_default="yellow", nullable=False),
        sa.Column("location", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.book_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("annotation_id"),
    )
    op.create_index(op.f("ix_annotations_book_id"), "annotations", ["book_id"], unique=False)
    op.create_index(op.f("ix_annotations_owner_user_id"), "annotations", ["owner_user_id"], unique=False)
    op.create_table(
        "book_shelves",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("shelf_id", sa.Uuid(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("id = book_id::text || ':' || shelf_id::text", name="ck_book_shelves_pair_id"),
        sa.ForeignKeyConstraint(["book_id"], ["books.book_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shelf_id"], ["shelves.shelf_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", "shelf_id"),
    )
    op.create_index(op.f("ix_book_shelves_book_id"), "book_shelves", ["book_id"], unique=False)
    op.create_index(op.f("ix_book_shelves_owner_user_id"), "book_shelves", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_book_shelves_shelf_id"), "book_shelves", ["shelf_id"], unique=False)
    op.create_table(
        "book_tags",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("id = book_id::text || ':' || tag_id::text", name="ck_book_tags_pair_id"),
        sa.ForeignKeyConstraint(["book_id"], ["books.book_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.tag_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", "tag_id"),
    )
    op.create_index(op.f("ix_book_tags_book_id"), "book_tags", ["book_id"], unique=False)
    op.create_index(op.f("ix_book_tags_owner_user_id"), "book_tags", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_book_tags_tag_id"), "book_tags", ["tag_id"], unique=False)
    op.create_table(
        "notes",
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("location", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("is_pinned", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.book_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("note_id"),
    )
    op.create_index(op.f("ix_notes_book_id"), "notes", ["book_id"], unique=False)
    op.create_index(op.f("ix_notes_owner_user_id"), "notes", ["owner_user_id"], unique=False)
    op.add_column("books", sa.Column("publication_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("books", sa.Column("file_format", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("file_size", sa.BigInteger(), nullable=True))
    op.add_column("books", sa.Column("file_hash", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("is_physical", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("books", sa.Column("physical_location", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("lent_to", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("lent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("books", sa.Column("series_id", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("series_name", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("series_number", sa.Float(), nullable=True))
    op.add_column("books", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("books", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("books", sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True))

    _backfill_book_metadata()


def _backfill_book_metadata() -> None:
    """Promote valid legacy values without rewriting the original envelope.

    PostgreSQL 17 is the deployment baseline. Invalid historical values stay
    available in custom_metadata instead of preventing the migration. Naive
    timestamps were emitted by older clients and are interpreted as UTC.
    """
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    types = {
        "publication_date": "timestamptz",
        "file_format": "text",
        "file_size": "bigint",
        "file_hash": "text",
        "is_physical": "boolean",
        "physical_location": "text",
        "lent_to": "text",
        "lent_at": "timestamptz",
        "series_id": "text",
        "series_name": "text",
        "series_number": "double precision",
        "started_at": "timestamptz",
        "completed_at": "timestamptz",
        "last_read_at": "timestamptz",
    }

    for field, sql_type in types.items():
        value = f"custom_metadata ->> '{field}'"
        condition = (
            f"jsonb_typeof(custom_metadata -> '{field}') = 'string'"
            if sql_type == "text"
            else f"pg_input_is_valid({value}, '{sql_type}')"
        )
        op.execute(f"UPDATE books SET {field} = ({value})::{sql_type} WHERE {value} IS NOT NULL AND {condition}")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("books", "last_read_at")
    op.drop_column("books", "completed_at")
    op.drop_column("books", "started_at")
    op.drop_column("books", "series_number")
    op.drop_column("books", "series_name")
    op.drop_column("books", "series_id")
    op.drop_column("books", "lent_at")
    op.drop_column("books", "lent_to")
    op.drop_column("books", "physical_location")
    op.drop_column("books", "is_physical")
    op.drop_column("books", "file_hash")
    op.drop_column("books", "file_size")
    op.drop_column("books", "file_format")
    op.drop_column("books", "publication_date")
    op.drop_index(op.f("ix_notes_owner_user_id"), table_name="notes")
    op.drop_index(op.f("ix_notes_book_id"), table_name="notes")
    op.drop_table("notes")
    op.drop_index(op.f("ix_book_tags_tag_id"), table_name="book_tags")
    op.drop_index(op.f("ix_book_tags_owner_user_id"), table_name="book_tags")
    op.drop_index(op.f("ix_book_tags_book_id"), table_name="book_tags")
    op.drop_table("book_tags")
    op.drop_index(op.f("ix_book_shelves_shelf_id"), table_name="book_shelves")
    op.drop_index(op.f("ix_book_shelves_owner_user_id"), table_name="book_shelves")
    op.drop_index(op.f("ix_book_shelves_book_id"), table_name="book_shelves")
    op.drop_table("book_shelves")
    op.drop_index(op.f("ix_annotations_owner_user_id"), table_name="annotations")
    op.drop_index(op.f("ix_annotations_book_id"), table_name="annotations")
    op.drop_table("annotations")
    op.drop_index(op.f("ix_tags_owner_user_id"), table_name="tags")
    op.drop_table("tags")
    op.drop_index(op.f("ix_sync_tombstones_owner_user_id"), table_name="sync_tombstones")
    op.drop_table("sync_tombstones")
    op.drop_index(op.f("ix_shelves_owner_user_id"), table_name="shelves")
    op.drop_table("shelves")
