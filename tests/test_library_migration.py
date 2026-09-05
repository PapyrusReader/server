"""Run the library revision against the isolated pytest database only."""

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from papyrus.models import Base


async def test_library_revision_backfill_and_metadata(db_session, auth_user):
    path = Path(__file__).parents[1] / "alembic/versions/dcd3b384e6a4_add_owned_library_sync_and_promoted_.py"
    spec = importlib.util.spec_from_file_location("library_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    def migrate(session, fn):
        with Operations.context(MigrationContext.configure(session.connection())):
            fn()

    await db_session.run_sync(lambda session: migrate(session, revision.downgrade))
    envelope = '{"publication_date":"2020-01-02T03:04:05Z","file_size":42,"is_physical":true,"series_id":"descriptor","custom_metadata":{"keep":"value"}}'
    await db_session.execute(
        text(
            'INSERT INTO books (book_id, owner_user_id, title, custom_metadata) VALUES (:id, :owner, \'Legacy\', CAST(:metadata AS jsonb)), (:bad_id, :owner, \'Invalid\', \'{"publication_date":"invalid","file_size":"nope"}\'::jsonb)'
        ),
        {"id": uuid4(), "bad_id": uuid4(), "owner": auth_user["user_id"], "metadata": envelope},
    )
    await db_session.run_sync(lambda session: migrate(session, revision.upgrade))
    row = (
        await db_session.execute(
            text(
                "SELECT file_size, is_physical, series_id, custom_metadata->'custom_metadata' FROM books WHERE title = 'Legacy'"
            )
        )
    ).one()
    assert row == (42, True, "descriptor", {"keep": "value"})
    invalid = (
        await db_session.execute(text("SELECT publication_date, file_size FROM books WHERE title = 'Invalid'"))
    ).one()
    assert invalid == (None, None)
    differences = await db_session.run_sync(
        lambda session: compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
    )
    assert differences == []
    await db_session.commit()
