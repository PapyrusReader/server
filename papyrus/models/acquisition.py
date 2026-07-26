"""Acquisition sources, download clients and automatic acquisition rules."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from papyrus.core.database import Base


class AcquisitionEndpoint(Base):
    """A private indexer, Prowlarr instance, or download client connection."""

    __tablename__ = "acquisition_endpoints"

    endpoint_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    download_root: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    credentials: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)
    settings: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AcquisitionRule(Base):
    """A saved search that can automatically submit matching releases."""

    __tablename__ = "acquisition_rules"

    rule_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    endpoint_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    download_client_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("acquisition_endpoints.endpoint_id", ondelete="SET NULL"),
        nullable=True,
    )
    filters: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AcquisitionJob(Base):
    """An auditable submission to a download client."""

    __tablename__ = "acquisition_jobs"
    __table_args__ = (
        Index("ix_acquisition_jobs_owner_status", "owner_user_id", "status"),
        Index("ix_acquisition_jobs_next_poll_at", "next_poll_at"),
    )

    job_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    endpoint_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("acquisition_endpoints.endpoint_id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("acquisition_rules.rule_id", ondelete="SET NULL"))
    book_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("books.book_id", name="fk_acquisition_jobs_book_id_books", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    client_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_basis_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    downloaded_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    download_speed_bytes_per_second: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    eta_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    selected_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
