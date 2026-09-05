"""Owned library rows and durable entity deletion markers."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from papyrus.core.database import Base


class OwnedLibraryRow:
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)


class LibraryEntity(OwnedLibraryRow):
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncShelf(LibraryEntity, Base):
    __tablename__ = "shelves"

    shelf_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    color_hex: Mapped[str | None] = mapped_column(Text)
    icon_code_point: Mapped[int | None] = mapped_column(Integer)
    icon_font_family: Mapped[str | None] = mapped_column(Text)
    icon_font_package: Mapped[str | None] = mapped_column(Text)
    icon_match_text_direction: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    parent_shelf_id: Mapped[UUID | None] = mapped_column(ForeignKey("shelves.shelf_id", ondelete="SET NULL"))
    is_smart: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    smart_query: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class SyncTag(LibraryEntity, Base):
    __tablename__ = "tags"

    tag_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    color_hex: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class SyncNote(LibraryEntity, Base):
    __tablename__ = "notes"

    note_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.book_id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    location: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class SyncAnnotation(LibraryEntity, Base):
    __tablename__ = "annotations"

    annotation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.book_id", ondelete="CASCADE"), index=True)
    selected_text: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(Text, default="yellow", server_default="yellow")
    location: Mapped[dict[str, object]] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)


class SyncBookmark(LibraryEntity, Base):
    __tablename__ = "bookmarks"
    __table_args__ = (CheckConstraint("position >= 0 AND position <= 1", name="ck_bookmarks_position_range"),)

    bookmark_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.book_id", ondelete="CASCADE"), index=True)
    position: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    page_number: Mapped[int | None] = mapped_column(Integer)
    chapter_title: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    color_hex: Mapped[str] = mapped_column(Text, default="#FF5722", server_default="#FF5722")


class SyncBookShelf(OwnedLibraryRow, Base):
    __tablename__ = "book_shelves"
    __table_args__ = (
        UniqueConstraint("book_id", "shelf_id"),
        CheckConstraint("id = book_id::text || ':' || shelf_id::text", name="ck_book_shelves_pair_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.book_id", ondelete="CASCADE"), index=True)
    shelf_id: Mapped[UUID] = mapped_column(ForeignKey("shelves.shelf_id", ondelete="CASCADE"), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class SyncBookTag(OwnedLibraryRow, Base):
    __tablename__ = "book_tags"
    __table_args__ = (
        UniqueConstraint("book_id", "tag_id"),
        CheckConstraint("id = book_id::text || ':' || tag_id::text", name="ck_book_tags_pair_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.book_id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[UUID] = mapped_column(ForeignKey("tags.tag_id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncTombstone(OwnedLibraryRow, Base):
    __tablename__ = "sync_tombstones"

    table_name: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
