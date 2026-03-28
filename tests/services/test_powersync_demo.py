"""Service tests for the PowerSync sandbox demo."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.core.exceptions import ForbiddenError
from papyrus.models import PowerSyncDemoItem, User
from papyrus.schemas.powersync_demo import PowerSyncUploadMutation
from papyrus.services import powersync_demo as powersync_demo_service


async def _create_user(session: AsyncSession, email: str) -> User:
    user = User(
        display_name=email.split("@", 1)[0],
        primary_email=email,
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


async def test_apply_upload_batch_creates_updates_and_deletes_demo_items(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """PowerSync CRUD batches are applied to the demo source table."""
    async with test_session_maker() as session:
        user = await _create_user(session, "powersync@example.com")
        item_id = str(uuid4())

        created = await powersync_demo_service.apply_upload_batch(
            session,
            user.user_id,
            [
                PowerSyncUploadMutation(
                    type="demo_items",
                    op="PUT",
                    id=item_id,
                    data={"title": "Created", "notes": "From upload"},
                )
            ],
        )

        assert created == 1

        updated = await powersync_demo_service.apply_upload_batch(
            session,
            user.user_id,
            [
                PowerSyncUploadMutation(
                    type="demo_items",
                    op="PATCH",
                    id=item_id,
                    data={"title": "Updated"},
                )
            ],
        )

        assert updated == 1

        items = await powersync_demo_service.list_demo_items(session, user.user_id)
        assert len(items) == 1
        assert items[0].title == "Updated"
        assert items[0].notes == "From upload"

        deleted = await powersync_demo_service.apply_upload_batch(
            session,
            user.user_id,
            [PowerSyncUploadMutation(type="demo_items", op="DELETE", id=item_id)],
        )

        assert deleted == 1
        assert await powersync_demo_service.list_demo_items(session, user.user_id) == []


async def test_apply_upload_batch_rejects_other_users_demo_items(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Users cannot mutate source rows owned by a different account."""
    async with test_session_maker() as session:
        owner = await _create_user(session, "owner@example.com")
        intruder = await _create_user(session, "intruder@example.com")
        item = PowerSyncDemoItem(
            item_id=uuid4(),
            owner_user_id=owner.user_id,
            title="Owner Item",
            notes="Private",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(item)
        await session.commit()

        with pytest.raises(ForbiddenError):
            await powersync_demo_service.apply_upload_batch(
                session,
                intruder.user_id,
                [
                    PowerSyncUploadMutation(
                        type="demo_items",
                        op="PATCH",
                        id=str(item.item_id),
                        data={"title": "Intrusion"},
                    )
                ],
            )
