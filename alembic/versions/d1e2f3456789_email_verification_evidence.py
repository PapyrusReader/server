"""Bind verification to email and retain only attributable legacy evidence.

Password-only verified accounts retain Papyrus evidence. Matching retained Google
Gmail identities retain Google evidence. Other unattributable booleans need proof. No users,
credentials or library data are deleted. The activation audit reports the count
of previously verified accounts that now need proof. Rollback must fix forward.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1e2f3456789"
down_revision: str | Sequence[str] | None = "c0d1e2f34567"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRUSTED_PASSWORD = """
    primary_email_verified = true AND primary_email IS NOT NULL
    AND EXISTS (SELECT 1 FROM password_credentials p WHERE p.user_id = users.user_id)
    AND NOT EXISTS (SELECT 1 FROM user_identities i WHERE i.user_id = users.user_id)
"""


TRUSTED_GOOGLE = """
    primary_email_verified = true AND primary_email IS NOT NULL
    AND EXISTS (
        SELECT 1 FROM user_identities i WHERE i.user_id = users.user_id
        AND i.provider = 'google' AND i.provider_subject <> ''
        AND i.email_at_provider = users.primary_email
        AND i.email_at_provider LIKE '%@gmail.com'
    )
"""

TRUSTED_LEGACY = f"({TRUSTED_PASSWORD}) OR ({TRUSTED_GOOGLE})"


def upgrade() -> None:
    for name, kind in (
        ("verified_email", sa.String(255)),
        ("verification_source", sa.String(32)),
        ("verification_subject", sa.String(255)),
        ("verified_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("users", sa.Column(name, kind, nullable=True))

    for table in ("user_identities", "auth_exchange_codes"):
        op.add_column(table, sa.Column("hosted_domain", sa.String(255), nullable=True))

    op.add_column("email_action_tokens", sa.Column("email", sa.String(255), nullable=True))

    affected = op.get_bind().scalar(
        sa.text(f"SELECT count(*) FROM users WHERE primary_email_verified = true AND NOT ({TRUSTED_LEGACY})"),
    )

    logging.getLogger("alembic.runtime.migration").info(
        "Verification evidence audit: %s accounts require fresh proof", affected
    )

    op.execute(
        sa.text(
            f"UPDATE users SET verified_email = primary_email, verification_source = 'papyrus_email', "
            f"verified_at = CURRENT_TIMESTAMP WHERE {TRUSTED_PASSWORD}",
        ),
    )

    op.execute(
        sa.text(
            f"""
            UPDATE users SET verified_email = primary_email, verification_source = 'google',
                verification_subject = (
                    SELECT i.provider_subject FROM user_identities i
                    WHERE i.user_id = users.user_id AND i.provider = 'google'
                    AND i.email_at_provider = users.primary_email AND i.provider_subject <> ''
                    ORDER BY i.created_at, i.identity_id LIMIT 1
                ),
                verified_at = CURRENT_TIMESTAMP
            WHERE {TRUSTED_GOOGLE}
            """,
        ),
    )

    op.execute("UPDATE users SET primary_email_verified = false WHERE verified_email IS NULL")
    op.execute("UPDATE email_action_tokens SET used_at = CURRENT_TIMESTAMP WHERE action_type = 'verify_email'")

    op.execute(
        "UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE revoked_at IS NULL "
        "AND user_id IN (SELECT user_id FROM users WHERE verified_email IS NULL)",
    )

    op.create_table(
        "verification_recipients",
        sa.Column("email", sa.String(255), primary_key=True),
        sa.Column("last_queued_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "verification_deliveries",
        sa.Column(
            "token_id", sa.Uuid(), sa.ForeignKey("email_action_tokens.token_id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index("ix_verification_deliveries_user_id", "verification_deliveries", ["user_id"])
    op.create_index("ix_verification_deliveries_next_attempt_at", "verification_deliveries", ["next_attempt_at"])

    op.execute(
        """
        DO $$
        DECLARE permission record;
        BEGIN
            REVOKE ALL ON TABLE verification_deliveries, verification_recipients FROM PUBLIC;
            FOR permission IN
                SELECT DISTINCT relation.relname, role.rolname
                FROM pg_class relation
                JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                CROSS JOIN LATERAL aclexplode(relation.relacl) acl
                JOIN pg_roles role ON role.oid = acl.grantee
                WHERE namespace.nspname = current_schema()
                  AND relation.relname IN ('verification_deliveries', 'verification_recipients')
                  AND acl.grantee <> relation.relowner
            LOOP
                EXECUTE format('REVOKE ALL ON TABLE %I FROM %I CASCADE', permission.relname, permission.rolname);
            END LOOP;
        END $$
        """,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Verification enforcement must be retained. Fix forward without removing evidence or pending accounts."
    )
