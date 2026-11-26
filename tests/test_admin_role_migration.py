from pathlib import Path
from runpy import run_path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.mark.migration
async def test_existing_account_receives_constrained_user_role(db_session, auth_user):
    revision = run_path(str(Path(__file__).parents[1] / "alembic/versions/e35c054116ef_add_account_role.py"))

    def round_trip(session):
        with Operations.context(MigrationContext.configure(session.connection())):
            revision["downgrade"]()
            revision["upgrade"]()

    await db_session.run_sync(round_trip)
    params = {"id": auth_user["user_id"]}
    assert await db_session.scalar(text("SELECT role FROM users WHERE user_id = :id"), params) == "user"

    with pytest.raises(IntegrityError):
        await db_session.execute(text("UPDATE users SET role = 'other' WHERE user_id = :id"), params)

    await db_session.rollback()
