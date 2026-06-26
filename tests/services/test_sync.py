"""Service tests for production PowerSync upload handling."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.core.exceptions import ForbiddenError
from papyrus.models import SyncBook, User
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.services import sync as sync_service


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


async def _create_book(session: AsyncSession, user: User, title: str = "Existing Book") -> SyncBook:
    book = SyncBook(
        book_id=uuid4(),
        owner_user_id=user.user_id,
        title=title,
        author="Author",
        added_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(book)
    await session.flush()
    return book


async def test_apply_powersync_upload_batch_handles_book_mutations(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """PowerSync upload batches create books."""
    async with test_session_maker() as session:
        user = await _create_user(session, "sync@example.com")
        book_id = str(uuid4())
        applied_count = await sync_service.apply_powersync_upload_batch(
            session,
            user.user_id,
            [
                PowerSyncCrudMutation(
                    type="books",
                    op="PUT",
                    id=book_id,
                    data={
                        "title": "Synced Book",
                        "author": "Sync Author",
                        "reading_status": "in_progress",
                        "current_position": 0.4,
                    },
                ),
            ],
        )

        assert applied_count == 1
        book = await session.get(SyncBook, book_id)
        assert book is not None
        assert book.title == "Synced Book"


async def test_apply_powersync_upload_batch_rejects_cross_user_book_update(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """PowerSync upload handling enforces row ownership."""
    async with test_session_maker() as session:
        owner = await _create_user(session, "owner@example.com")
        intruder = await _create_user(session, "intruder@example.com")
        book = await _create_book(session, owner)
        await session.commit()

        with pytest.raises(ForbiddenError):
            await sync_service.apply_powersync_upload_batch(
                session,
                intruder.user_id,
                [
                    PowerSyncCrudMutation(
                        type="books",
                        op="PATCH",
                        id=str(book.book_id),
                        data={"title": "Intruder Edit"},
                    )
                ],
            )


async def test_apply_powersync_upload_batch_rejects_unknown_book_fields(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """The backend rejects fields outside the books upload contract."""
    async with test_session_maker() as session:
        await _create_user(session, "missing-book@example.com")

        with pytest.raises(PydanticValidationError):
            PowerSyncCrudMutation(
                type="books",
                op="PUT",
                id=str(uuid4()),
                data={"title": "Book", "unexpected": "value"},
            )
