"""Execute the managed acquisition revision against an existing submission."""

import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from papyrus.models import Base

pytestmark = pytest.mark.migration


async def test_managed_acquisition_upgrade_preserves_submission(db_session, auth_user):
    path = Path(__file__).parents[1] / "alembic/versions/a0c1456470b0_add_managed_acquisition_jobs.py"
    spec = importlib.util.spec_from_file_location("managed_acquisition_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    def migrate(session, operation):
        with Operations.context(MigrationContext.configure(session.connection())):
            operation()

    await db_session.run_sync(lambda session: migrate(session, revision.downgrade))
    job_id = uuid4()

    await db_session.execute(
        text(
            "INSERT INTO acquisition_jobs (job_id, owner_user_id, title, download_url) "
            "VALUES (:id, :owner, 'Existing submission', 'magnet:?xt=urn:btih:existing')"
        ),
        {"id": job_id, "owner": UUID(auth_user["user_id"])},
    )

    await db_session.run_sync(lambda session: migrate(session, revision.upgrade))

    row = (
        await db_session.execute(
            text("SELECT title, download_url, retry_count, book_id FROM acquisition_jobs WHERE job_id = :id"),
            {"id": job_id},
        )
    ).one()

    assert row == ("Existing submission", "magnet:?xt=urn:btih:existing", 0, None)
    await db_session.execute(text("UPDATE acquisition_jobs SET download_url = NULL WHERE job_id = :id"), {"id": job_id})

    differences = await db_session.run_sync(
        lambda session: compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
    )

    assert differences == []
    await db_session.run_sync(lambda session: migrate(session, revision.downgrade))

    restored_url = await db_session.scalar(
        text("SELECT download_url FROM acquisition_jobs WHERE job_id = :id"),
        {"id": job_id},
    )

    assert restored_url == f"managed-job:{job_id}"
    await db_session.commit()
