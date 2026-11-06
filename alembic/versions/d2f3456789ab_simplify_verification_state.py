"""Keep one account verification state and derive outbox metadata from its token.

Retain the preceding revision so already-applied review/staging databases can
upgrade without losing evidence, pending deliveries, accounts or library data.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2f3456789ab"
down_revision: str | Sequence[str] | None = "d1e2f3456789"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users SET verified_at = NULL, verification_source = NULL
        WHERE (
            primary_email_verified AND primary_email IS NOT NULL
            AND verified_email = primary_email AND verified_at IS NOT NULL
            AND verification_source IN ('papyrus_email', 'google')
            AND (verification_source <> 'google' OR verification_subject IS NOT NULL)
        ) IS NOT TRUE
        """,
    )

    op.execute(
        "UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE revoked_at IS NULL "
        "AND user_id IN (SELECT user_id FROM users WHERE verified_at IS NULL)",
    )

    op.alter_column("users", "verified_at", new_column_name="email_verified_at")
    op.alter_column("users", "verification_source", new_column_name="email_verification_source")

    for column in ("primary_email_verified", "verified_email", "verification_subject"):
        op.drop_column("users", column)

    op.create_check_constraint(
        "ck_users_email_verification",
        "users",
        "(email_verified_at IS NULL AND email_verification_source IS NULL) OR "
        "(primary_email IS NOT NULL AND email_verified_at IS NOT NULL "
        "AND email_verification_source IS NOT NULL "
        "AND email_verification_source IN ('papyrus_email', 'google'))",
    )

    op.drop_index("ix_verification_deliveries_user_id", table_name="verification_deliveries")

    for column in ("user_id", "recipient", "expires_at"):
        op.drop_column("verification_deliveries", column)


def downgrade() -> None:
    raise RuntimeError("Retain verification enforcement and pending deliveries. Fix forward.")
