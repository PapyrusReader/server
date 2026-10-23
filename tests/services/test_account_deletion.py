"""Account erasure preserves other owners and retries physical cleanup after commit."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.exceptions import ForbiddenError
from papyrus.models import (
    AccountMediaCleanup,
    AuthSession,
    PasswordCredential,
    SyncBook,
    SyncNote,
    SyncReadingActivity,
    User,
)
from papyrus.services import account_deletion, media, sync


async def test_purge_cascades_owned_data_and_retries_file_cleanup(
    db_session: AsyncSession,
    auth_user: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "media_storage_root", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "acquisition_import_root", tmp_path / "imports")
    user_id = UUID(auth_user["user_id"])
    other_id = uuid4()
    other = User(user_id=other_id, display_name="Other", primary_email="other@example.com")
    user = await db_session.get(User, user_id)
    assert user is not None
    user.disabled_at = datetime.now(UTC)
    user.deletion_due_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(other)
    await db_session.flush()
    book = SyncBook(owner_user_id=user_id, title="Deleted book")
    other_book = SyncBook(owner_user_id=other_id, title="Kept book")
    db_session.add_all([book, other_book])
    await db_session.flush()
    db_session.add(SyncNote(owner_user_id=user_id, book_id=book.book_id, title="Note", content="Private"))
    db_session.add(SyncReadingActivity(owner_user_id=user_id, payload={"book_id": str(book.book_id)}))
    await db_session.commit()
    other_book_id = other_book.book_id

    for root in (tmp_path / "media", tmp_path / "imports"):
        for owner in (user_id, other_id):
            directory = root / str(owner)
            directory.mkdir(parents=True)
            (directory / "book.epub").write_bytes(b"private")

    assert await account_deletion.purge_due_accounts(db_session) == 1
    assert await db_session.scalar(select(User).where(User.user_id == user_id)) is None
    assert await db_session.scalar(select(AuthSession).where(AuthSession.user_id == user_id)) is None
    assert await db_session.scalar(select(PasswordCredential).where(PasswordCredential.user_id == user_id)) is None
    assert await db_session.scalar(select(SyncNote).where(SyncNote.owner_user_id == user_id)) is None
    assert (
        await db_session.scalar(select(SyncReadingActivity).where(SyncReadingActivity.owner_user_id == user_id)) is None
    )
    assert await db_session.get(AccountMediaCleanup, user_id) is not None
    assert (tmp_path / "media" / str(user_id) / "book.epub").exists()

    original_cleanup = account_deletion._remove_owner_files

    def fail_cleanup(_: UUID) -> None:
        raise OSError("Storage is temporarily unavailable")

    monkeypatch.setattr(account_deletion, "_remove_owner_files", fail_cleanup)
    assert await account_deletion.cleanup_deleted_media(db_session) == 0
    assert await db_session.get(AccountMediaCleanup, user_id) is not None
    monkeypatch.setattr(account_deletion, "_remove_owner_files", original_cleanup)
    assert await account_deletion.cleanup_deleted_media(db_session) == 1
    assert await db_session.get(AccountMediaCleanup, user_id) is None
    assert await db_session.get(User, other_id) is not None
    assert await db_session.get(SyncBook, other_book_id) is not None

    for root in (tmp_path / "media", tmp_path / "imports"):
        assert not (root / str(user_id)).exists()
        assert (root / str(other_id) / "book.epub").read_bytes() == b"private"

    assert await account_deletion.purge_due_accounts(db_session) == 0
    assert await account_deletion.cleanup_deleted_media(db_session) == 0


async def test_pending_account_blocks_queued_sync_and_media_and_is_not_purged_early(
    db_session: AsyncSession, auth_user: dict[str, str]
) -> None:
    user_id = UUID(auth_user["user_id"])
    user = await db_session.get(User, user_id)
    assert user is not None
    user.disabled_at = datetime.now(UTC)
    user.deletion_due_at = datetime.now(UTC) + timedelta(days=1)
    await db_session.commit()
    assert await account_deletion.purge_due_accounts(db_session) == 0

    with pytest.raises(ForbiddenError):
        await sync.apply_powersync_upload_batch(db_session, user_id, [])

    with pytest.raises(ForbiddenError):
        await media._lock_user_uploads(db_session, user_id)
