"""add acquisition endpoints, rules and jobs

Revision ID: d4e5f6a7b8c9
Revises: c3f8b2a9d1e4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3f8b2a9d1e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create private acquisition integration tables."""
    op.create_table(
        "acquisition_endpoints",
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("credentials", postgresql.JSONB(), nullable=True),
        sa.Column("settings", postgresql.JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("endpoint_id"),
    )
    op.create_index("ix_acquisition_endpoints_owner_user_id", "acquisition_endpoints", ["owner_user_id"])
    op.create_table(
        "acquisition_rules",
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column("endpoint_ids", postgresql.JSONB(), nullable=True),
        sa.Column("download_client_id", sa.Uuid(), nullable=True),
        sa.Column("filters", postgresql.JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["download_client_id"],
            ["acquisition_endpoints.endpoint_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("rule_id"),
    )
    op.create_index("ix_acquisition_rules_owner_user_id", "acquisition_rules", ["owner_user_id"])
    op.create_table(
        "acquisition_jobs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_id", sa.Uuid(), nullable=True),
        sa.Column("rule_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("download_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("client_reference", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["acquisition_endpoints.endpoint_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["acquisition_rules.rule_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_acquisition_jobs_owner_user_id", "acquisition_jobs", ["owner_user_id"])


def downgrade() -> None:
    """Drop acquisition tables."""
    op.drop_table("acquisition_jobs")
    op.drop_table("acquisition_rules")
    op.drop_table("acquisition_endpoints")
