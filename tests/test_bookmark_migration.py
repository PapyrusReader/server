"""Verify bookmark migration structure in the isolated pytest database."""

import importlib.util
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from papyrus.models import Base


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
    differences = await db_session.run_sync(
        lambda session: compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
    )
    assert differences == []
    constraints = await db_session.run_sync(
        lambda session: inspect(session.connection()).get_check_constraints("bookmarks")
    )
    assert {constraint["name"] for constraint in constraints} == {"ck_bookmarks_position_range"}
    await db_session.commit()
