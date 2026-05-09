"""Service tests for production PowerSync upload handling."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import SyncAnnotation, SyncBook, SyncReadingSession, User
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


async def test_apply_powersync_upload_batch_handles_domain_mutations(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """PowerSync upload batches create and update the first production sync tables."""
    async with test_session_maker() as session:
        user = await _create_user(session, "sync@example.com")
        book_id = str(uuid4())
        annotation_id = str(uuid4())
        reading_session_id = str(uuid4())

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
                PowerSyncCrudMutation(
                    type="annotations",
                    op="PUT",
                    id=annotation_id,
                    data={
                        "book_id": book_id,
                        "selected_text": "Important passage",
                        "highlight_color": "#FFEB3B",
                        "start_position": "cfi-start",
                        "end_position": "cfi-end",
                    },
                ),
                PowerSyncCrudMutation(
                    type="reading_sessions",
                    op="PUT",
                    id=reading_session_id,
                    data={
                        "book_id": book_id,
                        "start_time": "2026-05-09T12:00:00+00:00",
                        "end_time": "2026-05-09T12:30:00+00:00",
                        "pages_read": 12,
                    },
                ),
            ],
        )

        assert applied_count == 3
        book = await session.get(SyncBook, book_id)
        annotation = await session.get(SyncAnnotation, annotation_id)
        reading_session = await session.get(SyncReadingSession, reading_session_id)
        assert book is not None
        assert annotation is not None
        assert reading_session is not None
        assert book.title == "Synced Book"
        assert annotation.book_id == book.book_id
        assert reading_session.book_id == book.book_id


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


async def test_apply_powersync_upload_batch_rejects_missing_referenced_book(
    test_session_maker: async_sessionmaker[AsyncSession],
):
    """Child sync rows must reference an owned book."""
    async with test_session_maker() as session:
        user = await _create_user(session, "missing-book@example.com")

        with pytest.raises(ValidationError):
            await sync_service.apply_powersync_upload_batch(
                session,
                user.user_id,
                [
                    PowerSyncCrudMutation(
                        type="annotations",
                        op="PUT",
                        id=str(uuid4()),
                        data={
                            "book_id": str(uuid4()),
                            "selected_text": "Orphaned",
                            "start_position": "start",
                            "end_position": "end",
                        },
                    )
                ],
            )
