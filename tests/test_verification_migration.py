"""Evidence backfill and session preservation on the isolated database."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from papyrus.models import Base

pytestmark = pytest.mark.migration


async def test_verification_migration_preserves_trustworthy_evidence_and_data(db_session, caplog):
    path = Path(__file__).parents[1] / "alembic/versions/d1e2f3456789_email_verification_evidence.py"
    spec = importlib.util.spec_from_file_location("verification_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    await db_session.execute(text("DROP TABLE verification_deliveries, verification_recipients"))

    for column in ("verified_email", "verification_source", "verification_subject", "verified_at"):
        await db_session.execute(text(f"ALTER TABLE users DROP COLUMN {column}"))

    for table in ("user_identities", "auth_exchange_codes"):
        await db_session.execute(text(f"ALTER TABLE {table} DROP COLUMN hosted_domain"))

    await db_session.execute(text("ALTER TABLE email_action_tokens DROP COLUMN email"))
    identifiers = [uuid4() for _ in range(5)]

    for index, user_id in enumerate(identifiers):
        await db_session.execute(
            text(
                "INSERT INTO users (user_id, display_name, primary_email, primary_email_verified) VALUES (:id, 'Preserved', :email, :verified)"
            ),
            {
                "id": user_id,
                "email": f"reader{index}@gmail.com" if index == 4 else f"reader{index}@example.com",
                "verified": index != 3,
            },
        )
        await db_session.execute(
            text("INSERT INTO password_credentials (user_id, password_hash) VALUES (:id, 'unchanged-hash')"),
            {"id": user_id},
        )
        await db_session.execute(
            text(
                "INSERT INTO auth_sessions (session_id, user_id, refresh_token_hash, expires_at) VALUES (:sid, :id, :hash, now() + interval '1 day')"
            ),
            {"sid": uuid4(), "id": user_id, "hash": str(index)},
        )

        if index in (1, 2, 4):
            await db_session.execute(
                text(
                    "INSERT INTO user_identities (identity_id, user_id, provider, provider_subject, email_at_provider, email_verified_at_provider) VALUES (:identity, :id, 'google', :sub, :email, now())"
                ),
                {"identity": uuid4(), "id": user_id, "sub": str(index), "email": f"reader{index}@gmail.com"},
            )

    await db_session.execute(
        text(
            "INSERT INTO books (book_id, owner_user_id, title, current_cfi) VALUES (:book, :owner, 'Durable library', 'epub:0:123')"
        ),
        {"book": uuid4(), "owner": identifiers[1]},
    )

    def upgrade(session):
        with Operations.context(MigrationContext.configure(session.connection())):
            revision.upgrade()

    reader_role = "test_verification_" + uuid4().hex
    await db_session.execute(text(f'CREATE ROLE "{reader_role}"'))
    await db_session.execute(
        text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO "{reader_role}"')
    )
    await db_session.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO PUBLIC"))
    caplog.set_level("INFO", logger="alembic.runtime.migration")
    await db_session.run_sync(upgrade)
    assert "Verification evidence audit: 2 accounts require fresh proof" in caplog.text
    assert not await db_session.scalar(
        text("SELECT has_table_privilege(:role, 'verification_deliveries', 'SELECT')"),
        {"role": reader_role},
    )

    await db_session.execute(
        text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM "{reader_role}"')
    )
    await db_session.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM PUBLIC"))
    await db_session.execute(text(f'DROP ROLE "{reader_role}"'))
    rows = (
        await db_session.execute(
            text("SELECT primary_email_verified, verified_email, verification_source FROM users ORDER BY primary_email")
        )
    ).all()
    assert rows == [
        (True, "reader0@example.com", "papyrus_email"),
        (False, None, None),
        (False, None, None),
        (False, None, None),
        (True, "reader4@gmail.com", "google"),
    ]
    assert await db_session.scalar(text("SELECT count(*) FROM users")) == 5
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM password_credentials WHERE password_hash = 'unchanged-hash'")
        )
        == 5
    )
    assert await db_session.scalar(text("SELECT count(*) FROM user_identities")) == 3
    assert await db_session.scalar(text("SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL")) == 2
    assert (
        await db_session.scalar(
            text("SELECT verification_subject FROM users WHERE primary_email = 'reader4@gmail.com'"),
        )
        == "4"
    )
    assert await db_session.scalar(text("SELECT current_cfi FROM books")) == "epub:0:123"
    differences = await db_session.run_sync(
        lambda session: compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
    )
    assert differences == []
