"""add managed acquisition jobs

Revision ID: a0c1456470b0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-25 14:15:13.615466

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a0c1456470b0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add managed download state and endpoint path mapping."""
    op.add_column("acquisition_endpoints", sa.Column("download_root", sa.String(length=2048), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("book_id", sa.Uuid(), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("client_hash", sa.String(length=255), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("client_state", sa.String(length=64), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("progress_basis_points", sa.Integer(), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("downloaded_bytes", sa.BigInteger(), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("total_bytes", sa.BigInteger(), nullable=True))
    op.add_column(
        "acquisition_jobs",
        sa.Column("download_speed_bytes_per_second", sa.BigInteger(), nullable=True),
    )
    op.add_column("acquisition_jobs", sa.Column("eta_seconds", sa.BigInteger(), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("selected_file_path", sa.Text(), nullable=True))
    op.add_column(
        "acquisition_jobs",
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("acquisition_jobs", sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("lease_owner", sa.String(length=255), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "acquisition_jobs",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column("acquisition_jobs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("acquisition_jobs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("acquisition_jobs", "download_url", existing_type=sa.Text(), nullable=True)
    op.create_index(
        "ix_acquisition_jobs_next_poll_at",
        "acquisition_jobs",
        ["next_poll_at"],
        unique=False,
    )
    op.create_index(
        "ix_acquisition_jobs_owner_status",
        "acquisition_jobs",
        ["owner_user_id", "status"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_acquisition_jobs_book_id_books",
        "acquisition_jobs",
        "books",
        ["book_id"],
        ["book_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Restore the legacy acquisition job shape."""
    op.drop_constraint("fk_acquisition_jobs_book_id_books", "acquisition_jobs", type_="foreignkey")
    op.drop_index("ix_acquisition_jobs_owner_status", table_name="acquisition_jobs")
    op.drop_index("ix_acquisition_jobs_next_poll_at", table_name="acquisition_jobs")
    op.execute(
        """
        UPDATE acquisition_jobs
        SET download_url = 'managed-job:' || job_id::text
        WHERE download_url IS NULL
        """
    )
    op.alter_column("acquisition_jobs", "download_url", existing_type=sa.Text(), nullable=False)
    op.drop_column("acquisition_jobs", "cancelled_at")
    op.drop_column("acquisition_jobs", "completed_at")
    op.drop_column("acquisition_jobs", "updated_at")
    op.drop_column("acquisition_jobs", "started_at")
    op.drop_column("acquisition_jobs", "submitted_at")
    op.drop_column("acquisition_jobs", "lease_until")
    op.drop_column("acquisition_jobs", "lease_owner")
    op.drop_column("acquisition_jobs", "next_poll_at")
    op.drop_column("acquisition_jobs", "retry_count")
    op.drop_column("acquisition_jobs", "selected_file_path")
    op.drop_column("acquisition_jobs", "eta_seconds")
    op.drop_column("acquisition_jobs", "download_speed_bytes_per_second")
    op.drop_column("acquisition_jobs", "total_bytes")
    op.drop_column("acquisition_jobs", "downloaded_bytes")
    op.drop_column("acquisition_jobs", "progress_basis_points")
    op.drop_column("acquisition_jobs", "client_state")
    op.drop_column("acquisition_jobs", "client_hash")
    op.drop_column("acquisition_jobs", "book_id")
    op.drop_column("acquisition_endpoints", "download_root")
