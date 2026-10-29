"""Exercise the additive revision on the isolated pytest database."""

import importlib.util
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from papyrus.models import Base

pytestmark = pytest.mark.migration

REVISIONS = [
    "e6f7a8b9c012_add_account_deletion.py",
    "f7a8b9c0d123_add_account_notifications.py",
    "a8b9c0d1e234_order_account_notifications.py",
]


@pytest.mark.parametrize("filename", REVISIONS)
async def test_account_deletion_revision_preserves_existing_account(db_session, auth_user, filename):
    revisions = []

    for name in REVISIONS[REVISIONS.index(filename) :]:
        path = Path(__file__).parents[1] / "alembic/versions" / name
        spec = importlib.util.spec_from_file_location("account_deletion_revision", path)
        revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(revision)
        revisions.append(revision)

    def migrate(session):
        with Operations.context(MigrationContext.configure(session.connection())):
            for revision in reversed(revisions):
                revision.downgrade()

            for revision in revisions:
                revision.upgrade()

    await db_session.run_sync(migrate)

    row = (
        await db_session.execute(
            text("SELECT primary_email, deletion_due_at, disabled_at FROM users WHERE user_id = :user_id"),
            {"user_id": auth_user["user_id"]},
        )
    ).one()

    assert row == ("user@example.com", None, None)

    differences = await db_session.run_sync(
        lambda session: compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
    )

    assert differences == []
