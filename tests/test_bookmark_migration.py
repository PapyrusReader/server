"""Verify bookmark migration structure in the isolated pytest database."""

import importlib.util
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from papyrus.models import Base

pytestmark = pytest.mark.migration


async def test_bookmark_revision_upgrade_downgrade_and_metadata(db_session):
    path = Path(__file__).parents[1] / "alembic/versions/af0fea8d6317_add_owned_bookmark_sync.py"
    spec = importlib.util.spec_from_file_location("bookmark_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    def migrate(session, fn):
        with Operations.context(MigrationContext.configure(session.connection())):
            fn()

    await db_session.run_sync(lambda session: migrate(session, revision.downgrade))
    assert not await db_session.run_sync(lambda session: inspect(session.connection()).has_table("bookmarks"))
    await db_session.run_sync(lambda session: migrate(session, revision.upgrade))

    location_path = Path(__file__).parents[1] / "alembic/versions/b9c0d1e2f345_reader_bookmark_locations.py"
    location_spec = importlib.util.spec_from_file_location("bookmark_location_revision", location_path)
    location_revision = importlib.util.module_from_spec(location_spec)
    location_spec.loader.exec_module(location_revision)
    await db_session.run_sync(lambda session: migrate(session, location_revision.upgrade))

    differences = await db_session.run_sync(
        lambda session: compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
    )

    assert differences == []

    constraints = await db_session.run_sync(
        lambda session: inspect(session.connection()).get_check_constraints("bookmarks")
    )

    assert {constraint["name"] for constraint in constraints} == {"ck_bookmarks_position_range"}
    await db_session.commit()


async def test_reader_locator_column_upgrade_preserves_bookmarks(client, auth_headers, db_session):
    from uuid import uuid4

    from sqlalchemy import text

    from tests.api.routes.test_library_sync import mutation, upload

    book_id, bookmark_id = uuid4(), uuid4()
    response = await upload(
        client,
        auth_headers,
        mutation("books", book_id, {"title": "Book"}),
        mutation("bookmarks", bookmark_id, {"book_id": str(book_id), "position": 0.5, "note": "Keep this"}),
    )

    assert response.status_code == 200
    path = Path(__file__).parents[1] / "alembic/versions/b9c0d1e2f345_reader_bookmark_locations.py"
    spec = importlib.util.spec_from_file_location("reader_location_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    def migrate(session):
        with Operations.context(MigrationContext.configure(session.connection())):
            revision.downgrade()
            revision.upgrade()

    await db_session.run_sync(migrate)
    row = (await db_session.execute(text("SELECT position, note, reader_locator FROM bookmarks"))).one()
    assert row == (0.5, "Keep this", None)
    await db_session.commit()
