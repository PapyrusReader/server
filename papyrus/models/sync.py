"""Persisted domain models used by PowerSync."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from papyrus.core.database import Base


class SyncBook(Base):
    """Book source row synced to clients through PowerSync."""

    __tablename__ = "books"

    book_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    co_authors: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    isbn13: Mapped[str | None] = mapped_column(String(32), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    reading_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_cfi: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    custom_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SyncAnnotation(Base):
    """Annotation source row synced to clients through PowerSync."""

    __tablename__ = "annotations"

    annotation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False, index=True)
    selected_text: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    highlight_color: Mapped[str] = mapped_column(
        String(16), nullable=False, default="#FFEB3B", server_default="#FFEB3B"
    )
    start_position: Mapped[str] = mapped_column(Text, nullable=False)
    end_position: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chapter_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SyncReadingSession(Base):
    """Reading session source row synced to clients through PowerSync."""

    __tablename__ = "reading_sessions"

    session_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    pages_read: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    device_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
