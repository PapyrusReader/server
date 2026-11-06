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

    simplify_path = path.with_name("d2f3456789ab_simplify_verification_state.py")
    simplify_spec = importlib.util.spec_from_file_location("simplification_revision", simplify_path)
    simplification = importlib.util.module_from_spec(simplify_spec)
    simplify_spec.loader.exec_module(simplification)
    await db_session.execute(text("DROP TABLE verification_deliveries, verification_recipients"))
    await db_session.execute(text("ALTER TABLE users DROP CONSTRAINT ck_users_email_verification"))

    for column in ("email_verified_at", "email_verification_source"):
        await db_session.execute(text(f"ALTER TABLE users DROP COLUMN {column}"))

    await db_session.execute(text("ALTER TABLE users ADD COLUMN primary_email_verified boolean NOT NULL DEFAULT false"))

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
    token_id = uuid4()

    await db_session.execute(
        text(
            "INSERT INTO email_action_tokens (token_id, user_id, email, action_type, token_hash, expires_at) "
            "VALUES (:token, :owner, 'reader1@example.com', 'verify_email', 'preserved-hash', now() + interval '1 day')"
        ),
        {"token": token_id, "owner": identifiers[1]},
    )

    await db_session.execute(
        text(
            "INSERT INTO verification_deliveries (token_id, user_id, recipient, token, attempts, next_attempt_at, expires_at) "
            "SELECT token_id, user_id, email, 'preserved-secret', 2, now(), expires_at FROM email_action_tokens WHERE token_id = :token"
        ),
        {"token": token_id},
    )

    before = (
        await db_session.execute(
            text("SELECT user_id, email, expires_at FROM email_action_tokens WHERE token_id = :token"),
            {"token": token_id},
        )
    ).one()

    await db_session.execute(
        text(
            "UPDATE users SET primary_email_verified = true, verified_email = 'stale@example.com', "
            "verification_source = 'papyrus_email', verified_at = now() WHERE user_id = :user"
        ),
        {"user": identifiers[3]},
    )

    verified_at = await db_session.scalar(
        text("SELECT verified_at FROM users WHERE user_id = :user"),
        {"user": identifiers[0]},
    )

    def simplify(session):
        with Operations.context(MigrationContext.configure(session.connection())):
            simplification.upgrade()

    await db_session.run_sync(simplify)
    assert (
        await db_session.scalar(
            text("SELECT email_verified_at FROM users WHERE user_id = :user"),
            {"user": identifiers[0]},
        )
        == verified_at
    )
    after = (
        await db_session.execute(
            text("SELECT user_id, email, expires_at FROM email_action_tokens WHERE token_id = :token"),
            {"token": token_id},
        )
    ).one()
    assert after == before
    assert (await db_session.execute(text("SELECT token, attempts FROM verification_deliveries"))).one() == (
        "preserved-secret",
        2,
    )

    rows = (
        await db_session.execute(
            text(
                "SELECT email_verified_at IS NOT NULL, primary_email, email_verification_source FROM users ORDER BY primary_email"
            )
        )
    ).all()
    assert rows == [
        (True, "reader0@example.com", "papyrus_email"),
        (False, "reader1@example.com", None),
        (False, "reader2@example.com", None),
        (False, "reader3@example.com", None),
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
            text("SELECT provider_subject FROM user_identities WHERE email_at_provider = 'reader4@gmail.com'"),
        )
        == "4"
    )
    assert await db_session.scalar(text("SELECT current_cfi FROM books")) == "epub:0:123"
    differences = await db_session.run_sync(
        lambda session: compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
    )
    assert differences == []
