"""Assign all existing and new accounts the ordinary user role."""

import sqlalchemy as sa

from alembic import op

revision = "e35c054116ef"
down_revision = "d2f3456789ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(5), nullable=False, server_default="user"))
    op.create_check_constraint("ck_users_role", "users", "role IN ('user', 'admin')")


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
