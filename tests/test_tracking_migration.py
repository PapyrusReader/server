"""Tracking tables are additive and preserve an existing owned library."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from papyrus.models import Base, SyncBook, User


async def test_tracking_upgrade_preserves_library_and_matches_metadata(db_session):
    path = Path(__file__).parents[1] / "alembic/versions/b5c6d7e8f901_add_reading_activity_and_goals.py"
    spec = importlib.util.spec_from_file_location("tracking_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    def migrate(session, fn):
        with Operations.context(MigrationContext.configure(session.connection())):
            fn()

    await db_session.run_sync(lambda session: migrate(session, revision.downgrade))
    owner = User(
        user_id=uuid4(),
        display_name="Reader",
        primary_email="migration@example.com",
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    db_session.add(owner)
    await db_session.flush()
    book = SyncBook(
        book_id=uuid4(),
        owner_user_id=owner.user_id,
        title="Preserved",
        author="Author",
        added_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    identifier = book.book_id
    db_session.add(book)
    await db_session.flush()
    await db_session.run_sync(lambda session: migrate(session, revision.upgrade))
    differences = await db_session.run_sync(
        lambda session: compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
    )
    assert differences == []
    assert (await db_session.get(SyncBook, identifier)).title == "Preserved"
    for table in ("reading_goals", "reading_activities", "goal_periods"):
        assert await db_session.run_sync(lambda session, name=table: inspect(session.connection()).has_table(name))
    await db_session.commit()
