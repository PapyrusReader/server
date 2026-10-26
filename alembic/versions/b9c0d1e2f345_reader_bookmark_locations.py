"""Preserve exact reader locations on bookmarks."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b9c0d1e2f345"
down_revision: str | Sequence[str] | None = "a8b9c0d1e234"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bookmarks", sa.Column("reader_locator", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("bookmarks", "reader_locator")
